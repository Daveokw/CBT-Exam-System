import warnings
warnings.filterwarnings('ignore', category=UserWarning, module='pandas')
import streamlit as st
from db import get_db_connection
import pandas as pd
import numpy as np
import json
import time
from datetime import datetime
import plotly.express as px
import os
import random
from ai import AIUnavailable, ai_available, summarise_performance
from auth import verify_security_answer


def ensure_challenge_start(conn, result_id, user_id):
    """Persist the challenge deadline so a browser refresh cannot reset it."""
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        "UPDATE results SET sq_challenge_started_at=CURRENT_TIMESTAMP "
        "WHERE id=%s AND user_id=%s AND status='ongoing' AND sq_challenge_started_at IS NULL",
        (result_id, user_id),
    )
    conn.commit()
    cursor.execute(
        "SELECT sq_challenge_started_at FROM results WHERE id=%s AND user_id=%s AND status='ongoing'",
        (result_id, user_id),
    )
    row = cursor.fetchone()
    if not row or not row["sq_challenge_started_at"]:
        raise ValueError("The examination is no longer in progress.")
    started = row["sq_challenge_started_at"]
    return datetime.fromisoformat(started) if isinstance(started, str) else started


def pending_challenge_trigger(trigger_indices, passed_triggers, current_index):
    """Prevent navigation past an uncompleted identity checkpoint."""
    return next(
        (index for index in sorted(trigger_indices) if index <= current_index and index not in passed_triggers),
        None,
    )

def student_dashboard():
    st.title("Student Dashboard")

    if "current_student_page" not in st.session_state:
        st.session_state["current_student_page"] = "Take Exam"

    menu = ["Take Exam", "View Results"]
    current_idx = menu.index(st.session_state["current_student_page"]) if st.session_state["current_student_page"] in menu else 0
    choice = st.sidebar.radio("Student Menu", menu, index=current_idx)
    st.session_state["current_student_page"] = choice

    # Show "Exam Submitted" screen (overrides main content)
    if st.session_state.get("exam_just_submitted"):
        score_info = st.session_state["exam_just_submitted"]
        st.success("**Exam Submitted Successfully!**")

        def handle_view_results():
            del st.session_state["exam_just_submitted"]
            st.session_state["current_student_page"] = "View Results"

        st.button("View Results", type="primary", on_click=handle_view_results)
        return

    if choice == "Take Exam":
        take_exam()
    elif choice == "View Results":
        view_results()

def handle_malpractice(user_id, test_id, result_id, conn):
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        "SELECT malpractice_warnings FROM results "
        "WHERE id=%s AND user_id=%s AND test_id=%s AND status='ongoing'",
        (result_id, user_id, test_id),
    )
    res = cursor.fetchone()
    if res is None:
        raise ValueError("The examination is no longer in progress.")
    warnings = res['malpractice_warnings'] if res and res['malpractice_warnings'] else 0
    warnings += 1

    cursor.execute("""
        INSERT INTO malpractice_logs (user_id, test_id, description)
        VALUES (%s, %s, %s)
    """, (user_id, test_id, f"Focus Lost / Tab Switched. Warning Level: {warnings}"))
    cursor.execute(
        "UPDATE results SET malpractice_warnings=%s WHERE id=%s AND user_id=%s AND test_id=%s AND status='ongoing'",
        (warnings, result_id, user_id, test_id),
    )
    conn.commit()
    return warnings

def take_exam():
    st.header("Take Exam")
    conn = get_db_connection()

    active_test_id = st.session_state.get("active_test_id", -1)
    tests = pd.read_sql(
        "SELECT * FROM tests WHERE visible=TRUE OR id=?",
        conn,
        params=(active_test_id,),
    )

    if tests.empty:
        st.info("No exams available right now.")
        conn.close()
        return

    if "active_test_id" in st.session_state:
        test_id = st.session_state["active_test_id"]
        if test_id not in tests["id"].values:
            clear_session()
            st.error("This examination is no longer available.")
            conn.close()
            return
        st.info(f"Exam in progress: {tests[tests['id']==test_id]['title'].values[0]}")
    else:
        test_id = st.selectbox("Choose Exam:", tests["id"], format_func=lambda x: tests[tests["id"]==x]["title"].values[0])

    selected_test = tests[tests["id"]==test_id].iloc[0]
    user_id = st.session_state["user"]["id"]

    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT * FROM results
        WHERE user_id=%s AND test_id=%s
        ORDER BY id DESC LIMIT 1
    """, (user_id, int(test_id)))
    existing_result = cursor.fetchone()

    if existing_result and existing_result['status'] == 'completed':
        clear_session()
        st.warning("You have already completed this exam.")
        conn.close()
        return

    if existing_result and existing_result['status'] == 'ongoing':
        if "exam_started" not in st.session_state:
            st.toast("Resuming your session...")
            st.session_state["exam_started"] = True
            st.session_state["active_test_id"] = test_id
            st.session_state["result_id"] = existing_result['id']
            st.session_state["start_time"] = existing_result['start_time']
            saved_answers = json.loads(existing_result['saved_answers']) if existing_result['saved_answers'] else {}
            st.session_state["answers"] = saved_answers
            st.session_state["current_q_index"] = 0
            # Restore security question challenge state
            triggers_raw = existing_result.get('sq_trigger_indices')
            st.session_state["sq_trigger_indices"] = json.loads(triggers_raw) if triggers_raw else []
            passed_raw = existing_result.get('sq_passed_triggers')
            st.session_state["sq_passed_triggers"] = set(json.loads(passed_raw)) if passed_raw else set()

            sq_attempts_raw = existing_result.get('sq_attempts')
            st.session_state["sq_attempts"] = json.loads(sq_attempts_raw) if sq_attempts_raw else {}
    else:
        if "exam_started" not in st.session_state:
            if st.button("Start Exam"):
                conn.execute("BEGIN IMMEDIATE")
                if selected_test["max_students"]:
                    cursor.execute("SELECT COUNT(*) AS count FROM results WHERE test_id=%s", (int(test_id),))
                    if cursor.fetchone()["count"] >= selected_test["max_students"]:
                        conn.rollback()
                        st.warning("This examination has reached its student limit.")
                        conn.close()
                        return
                # Count questions so we can pick a valid trigger index
                cursor.execute("SELECT COUNT(*) as cnt FROM questions WHERE test_id=%s", (int(test_id),))
                q_count = cursor.fetchone()['cnt']
                if q_count == 0:
                    conn.rollback()
                    st.warning("This examination has no questions yet.")
                    conn.close()
                    return

                # Pick TWO trigger zones at random, then a specific index in each zone
                sq_trigger_indices = []
                if q_count > 0:
                    zones = {
                        'beginning': (0, max(0, int(q_count * 0.25) - 1)),
                        'middle':    (int(q_count * 0.25), int(q_count * 0.75) - 1),
                        'end':       (int(q_count * 0.75), q_count - 1),
                    }
                    valid_zones = [v for k, v in zones.items() if v[0] <= v[1]]
                    if len(valid_zones) >= 2:
                        chosen_zones = random.sample(valid_zones, 2)
                        sq_trigger_indices = [random.randint(z[0], z[1]) for z in chosen_zones]
                    elif len(valid_zones) == 1:
                        # Very short exam, just pick one trigger if possible
                        sq_trigger_indices = [random.randint(valid_zones[0][0], valid_zones[0][1])]

                cursor.execute("""
                    INSERT INTO results (user_id, test_id, score, total, status, start_time, saved_answers, sq_trigger_indices, sq_passed_triggers)
                    VALUES (%s, %s, 0, 0, 'ongoing', CURRENT_TIMESTAMP, '{}', %s, '[]')
                """, (user_id, int(test_id), json.dumps(sq_trigger_indices)))
                conn.commit()
                st.session_state["exam_started"] = True
                st.session_state["active_test_id"] = test_id
                st.session_state["result_id"] = cursor.lastrowid
                cursor.execute("SELECT start_time FROM results WHERE id=%s", (st.session_state["result_id"],))
                st.session_state["start_time"] = cursor.fetchone()["start_time"]
                st.session_state["answers"] = {}
                st.session_state["current_q_index"] = 0
                st.session_state["sq_trigger_indices"] = sq_trigger_indices
                st.session_state["sq_passed_triggers"] = set()
                st.session_state["sq_attempts"] = {}   # {question_id: attempts_used}
                st.rerun()
            else:
                conn.close()
                return

    # --- MALPRACTICE FIX: SAVE PROGRESS BEFORE AUTO-SUBMIT ---
    if st.button("MalpracticeTrigger", key="malpractice_btn"):
        warnings = handle_malpractice(user_id, int(test_id), st.session_state["result_id"], conn)
        if warnings == 1:
            st.error("**WARNING!** You left the exam tab. This has been recorded. Next time it will auto-submit!")
            time.sleep(3)
            st.rerun()
        elif warnings >= 2:
            st.error("**MULTIPLE VIOLATIONS.** Auto-submitting exam...")
            save_progress_to_db() # <--- THE FIX: Saves latest answer
            time.sleep(2)
            score_info = submit_exam(test_id, st.session_state["result_id"], conn, user_id)
            clear_session()
            st.session_state["exam_just_submitted"] = score_info
            st.rerun()

    js_code = """
    <script>
    function setupMalpracticeDetection() {
        let triggerBtn = null;
        let searchInterval = setInterval(() => {
            const buttons = window.parent.document.querySelectorAll('button');
            buttons.forEach(btn => {
                if (btn.innerText.includes('MalpracticeTrigger')) {
                    triggerBtn = btn;
                    let container = btn.closest('div[data-testid="stButton"]');
                    if(container) {
                        container.style.opacity = '0';
                        container.style.position = 'absolute';
                        container.style.pointerEvents = 'none';
                    }
                    clearInterval(searchInterval);
                }
            });
        }, 100);
        window.parent.document.addEventListener("visibilitychange", function() {
            if (window.parent.document.hidden && triggerBtn) { triggerBtn.click(); }
        });
    }
    setupMalpracticeDetection();
    </script>
    """
    st.iframe(js_code, height=1)

    # ================================================================
    # SECURITY QUESTION CHALLENGE STATE CHECK
    # ================================================================
    sq_trigger_indices = st.session_state.get("sq_trigger_indices", [])
    sq_passed_triggers  = st.session_state.get("sq_passed_triggers", set())
    current_idx_peek = st.session_state.get("current_q_index", 0)

    pending_trigger = pending_challenge_trigger(sq_trigger_indices, sq_passed_triggers, current_idx_peek)
    is_sq_active = pending_trigger is not None

    timer_key = f"sq_timer_start_{pending_trigger}"
    if is_sq_active:
        st.session_state[timer_key] = ensure_challenge_start(
            conn, st.session_state["result_id"], user_id
        )

    # --- TIMER LOGIC (Python strictly enforces it) ---
    now = datetime.now()
    if is_sq_active:
        now = st.session_state[timer_key] # Freeze exam time during challenge

    db_start = st.session_state["start_time"]
    if isinstance(db_start, str):
        db_start = datetime.strptime(db_start, '%Y-%m-%d %H:%M:%S')

    elapsed_seconds = (now - db_start).total_seconds()
    total_duration_seconds = selected_test['duration'] * 60
    remaining_seconds = total_duration_seconds - elapsed_seconds

    if remaining_seconds <= 0:
        st.error("Time is up! Submitting exam...")
        save_progress_to_db() # Also save here just in case!
        score_info = submit_exam(test_id, st.session_state["result_id"], conn, user_id)
        clear_session()
        st.session_state["exam_just_submitted"] = score_info
        st.rerun()
        return

    mins, secs = divmod(int(remaining_seconds), 60)

    if is_sq_active:
        timer_html = f"""
        <div style="background-color: #2e3b4e; color: #9ca3af; padding: 10px; border-radius: 8px; text-align: center; font-size: 22px; font-weight: bold; border: 2px dashed #9ca3af; font-family: monospace;">
            <span>Time Remaining: {mins}m {secs}s (PAUSED)</span>
        </div>
        """
    else:
        # --- LIVE VISUAL TIMER FIX (JavaScript keeps it ticking) ---
        timer_html = f"""
        <div style="background-color: #2e3b4e; color: #4ade80; padding: 10px; border-radius: 8px; text-align: center; font-size: 22px; font-weight: bold; border: 2px solid #4ade80; font-family: monospace;">
            <span id="live-clock">Time Remaining: {mins}m {secs}s</span>
        </div>
        <script>
            var timeLeft = {int(remaining_seconds)};
            var clockElement = document.getElementById('live-clock');
            var timer = setInterval(function() {{
                timeLeft--;
                if (timeLeft <= 0) {{
                    clearInterval(timer);
                    clockElement.innerHTML = "Time is up! Submitting...";
                    clockElement.style.color = "#f87171";
                    clockElement.style.borderColor = "#f87171";
                    // Force streamlit to refresh and trigger python's auto-submit
                    window.parent.location.reload();
                }} else {{
                    var m = Math.floor(timeLeft / 60);
                    var s = Math.floor(timeLeft % 60);
                    clockElement.innerHTML = "Time Remaining: " + m + "m " + s + "s";
                }}
            }}, 1000);
        </script>
        """
    st.iframe(timer_html, height=70)
    # -------------------------------------------------------------

    # ================================================================
    # SECURITY QUESTION CHALLENGE
    # Fires up to twice, at the pre-determined random trigger indices.
    # ================================================================
    if is_sq_active:
        # Fetch this student's security questions
        _sq_conn = get_db_connection()
        _sq_cur = _sq_conn.cursor(dictionary=True)
        _sq_cur.execute(
            "SELECT * FROM security_questions WHERE user_id=%s",
            (user_id,)
        )
        all_sq = _sq_cur.fetchall()
        _sq_cur.close()
        _sq_conn.close()

        if all_sq:
            # Pick 1 question at random (deterministic per trigger using result_id + trigger_index as seed)
            chosen_sq = random.Random(st.session_state["result_id"] + pending_trigger).choice(all_sq)

            st.warning(
                "**Security Verification Required**\n\n"
                "Please answer the following question to confirm your identity "
                "and continue your examination. "
                "You have **2 attempts** and **60 seconds**. "
                "Failing both attempts or running out of time will result in automatic submission of your examination.",
                icon="🔐"
            )

            # --- 60-Second Challenge Timer Logic ---
            timer_key = f"sq_timer_start_{pending_trigger}"
            if timer_key not in st.session_state:
                st.session_state[timer_key] = datetime.now()

            elapsed_seconds = (datetime.now() - st.session_state[timer_key]).total_seconds()
            time_remaining = max(0, 60 - elapsed_seconds)

            if time_remaining <= 0:
                # Time ran out
                _log_conn = get_db_connection()
                _log_cur = _log_conn.cursor()
                _log_cur.execute(
                    "INSERT INTO malpractice_logs (user_id, test_id, description) VALUES (%s, %s, %s)",
                    (user_id, int(test_id), "Security question challenge failed due to timeout — exam auto-submitted.")
                )
                _log_conn.commit()
                _log_cur.close()
                _log_conn.close()
                st.error("**Security verification timed out.** Your exam is being submitted automatically.")
                save_progress_to_db()
                time.sleep(2)
                score_info = submit_exam(test_id, st.session_state["result_id"], get_db_connection(), user_id)
                clear_session()
                st.session_state["exam_just_submitted"] = score_info
                st.rerun()
                return

            # Visual JS Timer
            sq_timer_html = f"""
            <div style="background-color: #2e3b4e; color: #facc15; padding: 10px; border-radius: 8px; text-align: center; font-size: 18px; font-weight: bold; border: 2px solid #facc15; font-family: monospace; margin-bottom: 10px;">
                <span id="sq-live-clock">Challenge Time Remaining: {int(time_remaining)}s</span>
            </div>
            <script>
                var sqTimeLeft = {int(time_remaining)};
                var sqClockElement = document.getElementById('sq-live-clock');
                var sqTimer = setInterval(function() {{
                    sqTimeLeft--;
                    if (sqTimeLeft <= 0) {{
                        clearInterval(sqTimer);
                        sqClockElement.innerHTML = "Time is up! Submitting...";
                        sqClockElement.style.color = "#f87171";
                        sqClockElement.parentElement.style.borderColor = "#f87171";
                        window.parent.location.reload();
                    }} else {{
                        sqClockElement.innerHTML = "Challenge Time Remaining: " + sqTimeLeft + "s";
                    }}
                }}, 1000);
            </script>
            """
            st.iframe(sq_timer_html, height=65)
            # ----------------------------------------

            sq_attempts = st.session_state.get("sq_attempts", {})
            sq_id = str(chosen_sq['id'])
            attempts_used = sq_attempts.get(sq_id, 0)

            if attempts_used >= 2:
                # Already failed both attempts for this question -> auto submit
                _log_conn = get_db_connection()
                _log_cur = _log_conn.cursor()
                _log_cur.execute(
                    "INSERT INTO malpractice_logs (user_id, test_id, description) VALUES (%s, %s, %s)",
                    (user_id, int(test_id), "Security question challenge failed — exam auto-submitted.")
                )
                _log_conn.commit()
                _log_cur.close()
                _log_conn.close()
                st.error("**Security verification failed.** Your exam is being submitted automatically.")
                save_progress_to_db()
                time.sleep(2)
                score_info = submit_exam(test_id, st.session_state["result_id"], get_db_connection(), user_id)
                clear_session()
                st.session_state["exam_just_submitted"] = score_info
                st.rerun()
                return

            # Show the challenge input for this question
            with st.form(key=f"sq_form_{sq_id}"):
                st.markdown(f"**{chosen_sq['question']}**")
                remaining = 2 - attempts_used
                st.caption(f"Attempts remaining: {remaining}")
                given_answer = st.text_input("Your Answer", key=f"sq_input_{sq_id}", autocomplete="off")
                sq_submitted = st.form_submit_button("Submit Answer")

            if sq_submitted:
                if verify_security_answer(chosen_sq['answer'], given_answer):
                    # Calculate time spent
                    time_spent = (datetime.now() - st.session_state[timer_key]).total_seconds()

                    # Mark this specific trigger point as passed
                    sq_passed_triggers.add(pending_trigger)
                    st.session_state["sq_passed_triggers"] = sq_passed_triggers

                    # Persist passed trigger to DB and update start_time
                    _upd_conn = get_db_connection()
                    _upd_cur = _upd_conn.cursor()
                    _upd_cur.execute(
                        "UPDATE results SET sq_passed_triggers=%s, sq_challenge_started_at=NULL, "
                        "start_time = datetime(start_time, '+' || %s || ' seconds') WHERE id=%s",
                        (json.dumps(list(sq_passed_triggers)), int(time_spent), st.session_state["result_id"])
                    )
                    _upd_conn.commit()
                    _upd_cur.close()
                    _upd_conn.close()

                    # Update session state start_time so python timer syncs
                    from datetime import timedelta
                    if isinstance(st.session_state["start_time"], str):
                        dt = datetime.strptime(st.session_state["start_time"], '%Y-%m-%d %H:%M:%S')
                        st.session_state["start_time"] = dt + timedelta(seconds=int(time_spent))
                    else:
                        st.session_state["start_time"] += timedelta(seconds=int(time_spent))

                    st.success("Correct! Identity verified. Continuing your examination...")
                    time.sleep(1)
                    st.rerun()
                else:
                    attempts_used += 1
                    sq_attempts[sq_id] = attempts_used
                    st.session_state["sq_attempts"] = sq_attempts

                    # Persist attempts to DB
                    _upd_conn = get_db_connection()
                    _upd_cur = _upd_conn.cursor()
                    _upd_cur.execute(
                        "UPDATE results SET sq_attempts=%s WHERE id=%s",
                        (json.dumps(sq_attempts), st.session_state["result_id"])
                    )
                    _upd_conn.commit()
                    _upd_cur.close()
                    _upd_conn.close()

                    if attempts_used >= 2:
                        st.error("Incorrect. You have used both attempts for this question.")
                    else:
                        st.error(f"Incorrect answer. You have {2 - attempts_used} attempt(s) remaining.")
                    st.rerun()

            # Stop rendering the rest of the page until challenge is resolved
            cursor.close()
            conn.close()
            return

    cursor.execute("SELECT * FROM questions WHERE test_id=%s", (int(test_id),))
    questions = cursor.fetchall()
    conn.close()

    if not questions:
        st.error("No questions found.")
        return

    total_q = len(questions)
    current_idx = st.session_state["current_q_index"]
    q = questions[current_idx]

    with st.sidebar:
        st.markdown("---")
        st.subheader("Question Palette")

        palette_cols = st.columns(5)
        for i in range(total_q):
            q_id_str = str(questions[i]['id'])
            is_answered = q_id_str in st.session_state["answers"]

            if i == current_idx:
                label = f"► {i+1}"
            elif is_answered:
                label = f"✓ {i+1}"
            else:
                label = f"{i+1}"

            btn_type = "primary" if i == current_idx else ("secondary" if is_answered else "tertiary")
            if palette_cols[i % 5].button(label, key=f"nav_q_{i}", type=btn_type):
                save_progress_to_db()
                st.session_state["current_q_index"] = i
                st.rerun()

    with st.container(border=True):
        st.subheader(f"Question {current_idx + 1} of {total_q}")
        st.markdown(f"### {q['question']}")

        if q.get('image_path') and os.path.exists(q['image_path']):
            st.image(q['image_path'], use_container_width=True)
            st.markdown("---")

        options = ["A", "B", "C", "D"]
        labels = [q['option_a'], q['option_b'], q['option_c'], q['option_d']]

        q_id_str = str(q['id'])
        previous_selection = st.session_state["answers"].get(q_id_str)
        radio_index = options.index(previous_selection) if previous_selection in options else None

        selected_option = st.radio(
            "Select an option:",
            options,
            index=radio_index,
            format_func=lambda x: f"{x}: {labels[options.index(x)]}",
            key=f"radio_{q['id']}"
        )

        if selected_option:
            st.session_state["answers"][q_id_str] = selected_option
            # Auto-save immediately when they click an option!
            save_progress_to_db()

    col1, col2, col3 = st.columns([1, 2, 1])

    with col1:
        if current_idx > 0:
            if st.button("Previous"):
                save_progress_to_db()
                st.session_state["current_q_index"] -= 1
                st.rerun()

    with col3:
        if current_idx < total_q - 1:
            if st.button("Next"):
                save_progress_to_db()
                st.session_state["current_q_index"] += 1
                st.rerun()
        else:
            if st.button("Submit Exam", type="primary"):
                save_progress_to_db()
                score_info = submit_exam(test_id, st.session_state["result_id"], get_db_connection(), user_id)
                clear_session()
                st.session_state["exam_just_submitted"] = score_info
                st.rerun()

def save_progress_to_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    json_str = json.dumps(st.session_state["answers"])
    cursor.execute(
        "UPDATE results SET saved_answers=%s WHERE id=%s AND user_id=%s AND status='ongoing'",
        (json_str, st.session_state["result_id"], st.session_state["user"]["id"]),
    )
    conn.commit()
    conn.close()

def submit_exam(test_id, result_id, conn, user_id):
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        "SELECT score, total, status, saved_answers FROM results "
        "WHERE id=%s AND test_id=%s AND user_id=%s",
        (result_id, int(test_id), user_id),
    )
    res = cursor.fetchone()
    if res is None:
        conn.close()
        raise ValueError("The examination result does not belong to this student and test.")
    if res["status"] == "completed":
        conn.close()
        return {"score": res["score"], "total": res["total"]}
    if res["status"] != "ongoing":
        conn.close()
        raise ValueError("The examination is not in progress.")
    cursor.execute("SELECT id, correct_option FROM questions WHERE test_id=%s", (int(test_id),))
    questions = cursor.fetchall()
    student_answers = json.loads(res['saved_answers']) if res['saved_answers'] else {}

    score = 0
    for q in questions:
        ans = student_answers.get(str(q['id']))
        if ans and ans == q['correct_option']:
            score += 1

    cursor.execute("""
        UPDATE results
        SET score=%s, total=%s, status='completed'
        WHERE id=%s AND test_id=%s AND user_id=%s AND status='ongoing'
    """, (score, len(questions), result_id, int(test_id), user_id))
    conn.commit()
    conn.close()
    return {"score": score, "total": len(questions)}

def clear_session():
    keys = [
        "exam_started", "active_test_id", "result_id", "start_time",
        "answers", "current_q_index",
        "sq_trigger_indices", "sq_passed_triggers", "sq_attempts", "sq_correct_ids",
    ]
    for k in keys:
        if k in st.session_state:
            del st.session_state[k]

    # Also clear any challenge timer keys
    timer_keys = [k for k in st.session_state.keys() if k.startswith("sq_timer_start_")]
    for tk in timer_keys:
        del st.session_state[tk]

def result_is_released(result):
    """Release capped tests once enough distinct students have completed them."""
    if result["release_option"] == "immediate":
        return True
    return (
        result["release_option"] == "after_limit"
        and result["max_students"] > 0
        and result["completed_count"] >= result["max_students"]
    )


def released_score_history(results):
    """Return chronological percentages for results whose scores are released."""
    release_mask = results.apply(result_is_released, axis=1)
    released = results.loc[
        release_mask & (results["total"] > 0),
        ["Paper", "score", "total", "date_taken"],
    ].copy()
    if released.empty:
        return released.assign(percentage=pd.Series(dtype=float))
    released["date_taken"] = pd.to_datetime(released["date_taken"], errors="coerce")
    released = released.dropna(subset=["date_taken"])
    released["percentage"] = (released["score"] / released["total"] * 100).round(1)
    return released.sort_values("date_taken")


def answer_review_rows(questions, saved_answers):
    """Build the answer review only after the caller checks the release settings."""
    return [
        {
            "Question": question["question"],
            "Your answer": saved_answers.get(str(question["id"]), "Not answered"),
            "Correct answer": question["correct_option"],
        }
        for question in questions
    ]


def load_student_results(conn, user_id):
    """Load a student's completed results with current per-test completion counts."""
    return pd.read_sql("""
        SELECT r.id as result_id, t.id as test_id, t.title as Paper, t.show_correct_answers,
               t.release_option, COALESCE(t.max_students, 0) as max_students,
               COALESCE(completed.completed_count, 0) as completed_count,
               r.score, r.total, r.status, r.date_taken, r.saved_answers
        FROM results r
        JOIN tests t ON r.test_id = t.id
        LEFT JOIN (
            SELECT test_id, COUNT(DISTINCT user_id) as completed_count
            FROM results
            WHERE status = 'completed'
            GROUP BY test_id
        ) completed ON completed.test_id = t.id
        WHERE r.user_id = ? AND r.status = 'completed'
        ORDER BY r.date_taken DESC
    """, conn, params=(user_id,))


def view_results():
    st.header("My Examination Report")
    conn = get_db_connection()
    user_id = st.session_state["user"]["id"]

    df_results = load_student_results(conn, user_id)

    if df_results.empty:
        st.info("You have not completed any examinations yet.")
        conn.close()
        return

    st.subheader("Examination History & Reports")
    history = released_score_history(df_results)
    if len(history) >= 2:
        st.subheader("Released Score Trend")
        st.line_chart(history, x="date_taken", y="percentage", x_label="Examination date", y_label="Score (%)")
        change = history.iloc[-1]["percentage"] - history.iloc[-2]["percentage"]
        st.caption(f"Most recent change: {change:+.1f} percentage points. Different tests may not be directly comparable.")

    # Let the user select which exam result to view
    result_options = {row["result_id"]: f"{row['Paper']} (Taken on {row['date_taken']})" for _, row in df_results.iterrows()}
    selected_result_id = st.selectbox("Select an Examination to View Summary:", list(result_options.keys()), format_func=lambda x: result_options[x])

    selected_row = df_results[df_results["result_id"] == selected_result_id].iloc[0]

    if result_is_released(selected_row):
        percentage = round(selected_row["score"] / selected_row["total"] * 100, 1) if selected_row["total"] > 0 else 0
        st.success(f"**Score:** {selected_row['score']}/{selected_row['total']} ({percentage}%)")
    elif selected_row["release_option"] == "do_not_release":
        st.info("The score for this examination has not been released by the administrator.")
    else:
        if selected_row["max_students"] > 0:
            st.info(
                "The score will be released when the student limit is reached "
                f"({selected_row['completed_count']}/{selected_row['max_students']} students completed)."
            )
        else:
            st.info("The administrator has not set a student limit, so this score cannot be released yet.")

    if not result_is_released(selected_row):
        conn.close()
        return

    st.markdown("---")

    # --- Topic-based text report ---
    st.subheader(f"Performance Report: {selected_row['Paper']}")
    st.caption("This report summarises your strengths and areas that require further attention based on your examination responses.")

    cursor = conn.cursor(dictionary=True)
    topic_data = []

    cursor.execute(
        "SELECT id, question, topic, subtopic, correct_option FROM questions WHERE test_id=%s",
        (int(selected_row["test_id"]),),
    )
    questions = cursor.fetchall()
    saved_ans = json.loads(selected_row["saved_answers"]) if selected_row["saved_answers"] else {}

    # Build a nested dict: topic -> subtopic -> {correct, total}
    # This lets us display specific sub-topics under each broad area in the report.
    topic_map = {}

    for q in questions:
        student_ans = saved_ans.get(str(q["id"]), None)
        is_correct = student_ans == q["correct_option"]
        raw_topic = q["topic"] if q["topic"] else "General"
        topic_label = "General Knowledge" if raw_topic.strip().lower() == "general" else raw_topic
        subtopic_label = q.get("subtopic") or None

        if topic_label not in topic_map:
            topic_map[topic_label] = {"correct": 0, "total": 0, "subtopics": {}}

        topic_map[topic_label]["total"] += 1
        if is_correct:
            topic_map[topic_label]["correct"] += 1

        # Track subtopic performance separately
        if subtopic_label:
            if subtopic_label not in topic_map[topic_label]["subtopics"]:
                topic_map[topic_label]["subtopics"][subtopic_label] = {"correct": 0, "total": 0}
            topic_map[topic_label]["subtopics"][subtopic_label]["total"] += 1
            if is_correct:
                topic_map[topic_label]["subtopics"][subtopic_label]["correct"] += 1

    conn.close()

    if selected_row["show_correct_answers"] and questions:
        with st.expander("Review answers"):
            st.dataframe(answer_review_rows(questions, saved_ans), hide_index=True, use_container_width=True)

    if not topic_map:
        st.info("No topic data available yet.")
        return

    # Compute accuracy per topic
    for t in topic_map:
        d = topic_map[t]
        d["accuracy"] = round(d["correct"] / d["total"] * 100, 1) if d["total"] > 0 else 0

    total_correct   = sum(d["correct"] for d in topic_map.values())
    total_attempted = sum(d["total"]   for d in topic_map.values())
    overall_pct     = round(total_correct / total_attempted * 100, 1) if total_attempted > 0 else 0

    best_topic  = max(topic_map, key=lambda t: topic_map[t]["accuracy"]) if topic_map else "N/A"
    worst_topic = min(topic_map, key=lambda t: topic_map[t]["accuracy"]) if topic_map else "N/A"

    strengths  = {t: d for t, d in topic_map.items() if d["accuracy"] >= 70}
    developing = {t: d for t, d in topic_map.items() if 50 <= d["accuracy"] < 70}
    weaknesses = {t: d for t, d in topic_map.items() if d["accuracy"] < 50}

    def format_topic_block(topics_dict):
        """Render a topic with its pinpointed sub-topics underneath."""
        lines = []
        for topic_name, data in sorted(topics_dict.items(), key=lambda x: -x[1]["accuracy"]):
            lines.append(f"**{topic_name}**")
            if data["subtopics"]:
                # List only the subtopics that had at least one question
                for sub, sub_data in sorted(data["subtopics"].items()):
                    lines.append(f"&nbsp;&nbsp;&nbsp;&nbsp;• {sub}")
            lines.append("")
        return lines

    # Build the full written report
    report_lines = []
    report_lines.append("**STUDENT PERFORMANCE REPORT**")
    report_lines.append("")
    report_lines.append(
        "Based on your recent examination, here is a summary of your performance "
        "across different subject areas, with specific topics pinpointed for your revision."
    )
    report_lines.append("")

    if strengths:
        report_lines.append("**Areas of Strength**")
        report_lines.extend(format_topic_block(strengths))

    if developing:
        report_lines.append("**Areas Progressing Well**")
        report_lines.extend(format_topic_block(developing))

    if weaknesses:
        report_lines.append("**Areas Requiring Improvement**")
        report_lines.extend(format_topic_block(weaknesses))

    # Summary advisory paragraph
    report_lines.append("**Summary**")
    summary_start = len(report_lines)
    if best_topic != worst_topic:
        report_lines.append(
            f"Your strongest subject is **{best_topic}** and the area that would benefit most from "
            f"additional revision is **{worst_topic}**."
        )
    if overall_pct >= 70:
        report_lines.append(
            "You are performing well overall. Continue your consistent revision to maintain this standard."
        )
    elif overall_pct >= 50:
        report_lines.append(
            "You are making reasonable progress. Focusing on the specific sub-topics listed under "
            "Areas Requiring Improvement will help you raise your overall performance significantly."
        )
    else:
        report_lines.append(
            "There is considerable room for improvement. It is strongly recommended that you revisit "
            "the specific sub-topics listed under Areas Requiring Improvement and seek guidance from "
            "your lecturer where necessary."
        )

    ai_advice = None
    if ai_available():
        advice_key = f"study_advice_{selected_result_id}"
        recent_scores = tuple(history["percentage"].tail(8).tolist())
        topic_signature = tuple(sorted((name, data["correct"], data["total"]) for name, data in topic_map.items()))
        advice_signature = (topic_signature, recent_scores)
        cached_advice = st.session_state.get(advice_key)
        if not isinstance(cached_advice, tuple) or cached_advice[0] != advice_signature:
            try:
                advice = summarise_performance(topic_map, recent_scores)
            except (AIUnavailable, ValueError):
                advice = None
            st.session_state[advice_key] = (advice_signature, advice)
        ai_advice = st.session_state[advice_key][1]
    if ai_advice:
        report_lines = report_lines[:summary_start] + [ai_advice]
    st.markdown("\n\n".join(report_lines))
    if ai_advice:
        st.caption("The summary uses AI-generated advice from topic totals and released score history; check it against your scores.")
