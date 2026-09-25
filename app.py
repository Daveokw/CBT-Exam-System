import streamlit as st
import warnings
# Suppress the pandas SQLAlchemy warning
warnings.filterwarnings('ignore', category=UserWarning, module='pandas')
from auth import DEMO_ADMIN_KEY, demo_mode_enabled, register_user, login_user, register_admin
from db import setup_database, get_db_connection
import admin
import student

# 1. Page Config
st.set_page_config(page_title="CBT System", layout="wide")

# 2. Setup DB (Runs once at startup)
setup_database()

if demo_mode_enabled():
    st.info("Public demo: use fictional names and answers only. Shared demo records may be reset.")

# Inject JS to disable browser autocomplete / password suggestions on all forms
st.iframe("""
<script>
function disableAutocomplete() {
    const forms = window.parent.document.querySelectorAll('form');
    forms.forEach(f => { f.setAttribute('autocomplete', 'off'); });
    const inputs = window.parent.document.querySelectorAll('input');
    inputs.forEach(i => {
        i.setAttribute('autocomplete', 'off');
        i.setAttribute('data-lpignore', 'true');
        i.setAttribute('data-form-type', 'other');
    });
}
setInterval(disableAutocomplete, 500);
</script>
""", height=1)

# 3. Session State Initialization
if "user" not in st.session_state:
    st.session_state["user"] = None
if "role" not in st.session_state:
    st.session_state["role"] = None

from auth import get_user_by_token, clear_user_token

if not st.session_state.get("user"):
    token = st.query_params.get("session")
    if token:
        user = get_user_by_token(token)
        if user:
            st.session_state["user"] = user
            st.session_state["role"] = user["role"]

def logout():
    if st.session_state.get("user"):
        clear_user_token(st.session_state["user"]["id"])
    st.session_state["user"] = None
    st.session_state["role"] = None
    st.query_params.clear()
    st.rerun()

# --- MAIN APP LOGIC ---

# ================= USER IS LOGGED IN =================
if st.session_state["user"]:
    # ------------------ SIDEBAR PROFILE ------------------
    with st.sidebar:
        st.header(f"{st.session_state['role'].title()} Profile")

        user = st.session_state["user"]

        # Display Student Details
        if st.session_state["role"] == "student":
            st.success(f"**{user['name']}**")
            st.write(f"**Matric:** {user['staff_id']}")
            st.write(f"**Dept:** {user.get('department', 'N/A')}")

        # Display Admin Details
        else:
            st.info(f"**{user['name']}**")
            st.write(f"**Staff ID:** {user['staff_id']}")

        st.markdown("---")
        if st.button("Logout", type="primary"):
            logout()

    # ------------------ DASHBOARD ROUTING ------------------
    if st.session_state["role"] == "admin":
        admin.admin_dashboard()
    else:
        student.student_dashboard()

# ================= USER IS NOT LOGGED IN =================
else:
    st.title("CBT System")

    # Sidebar Menu
    menu = ["Login", "Register"]
    choice = st.sidebar.radio("Menu", menu)

    col1, col2 = st.columns([1, 2]) # Centers the form visually

    # ------------------ LOGIN SECTION ------------------
    if choice == "Login":
        with col2:
            st.subheader("Sign In")

            role_choice = st.radio("Login as:", ["Student", "Admin"], horizontal=True)

            # Dynamic form key forces a fresh form when switching roles
            with st.form(f"login_form_{role_choice}"):
                if role_choice == "Student":
                    identifier = st.text_input("Matric Number (Numbers Only)", key=f"login_id_{role_choice}", autocomplete="off")
                else:
                    identifier = st.text_input("Staff ID (Numbers Only)", key=f"login_id_{role_choice}", autocomplete="off")

                # new-password prevents the browser from suggesting saved credentials
                password = st.text_input("Password", type="password", key=f"login_pw_{role_choice}", autocomplete="new-password")
                submitted = st.form_submit_button("Login")

            if submitted:
                errors = []
                if not identifier.isdigit() and identifier.lower() != "admin":
                    errors.append(f"{role_choice} ID must be numbers only.")

                if not identifier or not password:
                    errors.append("Please fill in all fields.")

                if errors:
                    for e in errors: st.error(e)
                else:
                    user = login_user(identifier, password, role_choice.lower())
                    if user:
                        if user['role'] == role_choice.lower():
                            st.session_state["user"] = user
                            st.session_state["role"] = user["role"]
                            st.query_params["session"] = user["session_token"]
                            st.toast(f"Welcome back, {user['name']}!")
                            st.rerun()
                        else:
                            st.error(f"This is not a {role_choice} account.")
                    else:
                        st.error("Invalid ID or Password")

    # ------------------ REGISTER SECTION ------------------
    elif choice == "Register":
        with col2:
            st.subheader("Create Account")

            reg_type = st.radio("Register as:", ["Student", "Admin"], horizontal=True)

            # ---- ADMIN REGISTRATION (unchanged, single step) ----
            if reg_type == "Admin":
                if demo_mode_enabled():
                    st.info(f"To try the administrator view, enter the demo key: {DEMO_ADMIN_KEY}")
                with st.form("reg_form_Admin", clear_on_submit=True):
                    name = st.text_input("Full Name", key="reg_name_Admin", autocomplete="off")
                    identifier = st.text_input("Staff ID (Numbers Only)", key="reg_id_Admin", autocomplete="off")
                    password = st.text_input("Password", type="password", key="reg_pw_Admin", autocomplete="new-password")
                    admin_key = st.text_input("Admin Secret Key", type="password", key="reg_key_Admin", autocomplete="new-password")
                    submitted = st.form_submit_button("Register")

                if submitted:
                    errors = []
                    if not name.replace(" ", "").isalpha():
                        errors.append("Name must contain only alphabets.")
                    if not identifier.isdigit():
                        errors.append("Staff ID must contain only numbers.")
                    if not name or not identifier or not password:
                        errors.append("All fields are required.")
                    if errors:
                        for e in errors: st.error(e)
                    elif not admin_key:
                        st.error("Admin Secret Key is required.")
                    else:
                        try:
                            register_admin(name, identifier, password, admin_key)
                            st.success("Admin Account Created! Please switch to Login.")
                        except Exception as e:
                            st.error(f"Error: {e}")

            # ---- STUDENT REGISTRATION (two-step) ----
            else:
                # The 10 fixed security questions every student must answer
                SECURITY_QUESTIONS = [
                    "What is the name of the primary school you attended?",
                    "What is your mother's maiden name?",
                    "What was the name of your first pet?",
                    "In what city were you born?",
                    "What is your oldest sibling's first name?",
                    "What is your father's first name?",
                    "What is your favourite food?",
                    "What was the name of your childhood best friend?",
                    "What street did you grow up on?",
                    "What was the name of your secondary school?",
                ]

                # Initialise step tracker; reset if user switches reg_type
                if st.session_state.get("reg_type_last") != "Student":
                    st.session_state["reg_step"] = 1
                    st.session_state["reg_pending"] = {}
                st.session_state["reg_type_last"] = "Student"
                if "reg_step" not in st.session_state:
                    st.session_state["reg_step"] = 1
                if "reg_pending" not in st.session_state:
                    st.session_state["reg_pending"] = {}

                # ---- STEP 1: Account Details ----
                if st.session_state["reg_step"] == 1:
                    st.markdown("**Step 1 of 2 — Account Details**")
                    with st.form("reg_form_Student_step1", clear_on_submit=False):
                        name = st.text_input("Full Name", key="reg_name_Student", autocomplete="off")
                        identifier = st.text_input("Matric Number (Numbers Only)", key="reg_id_Student", autocomplete="off")
                        department = st.text_input("Department", key="reg_dept_Student", autocomplete="off")
                        password = st.text_input("Password", type="password", key="reg_pw_Student", autocomplete="new-password")
                        submitted_step1 = st.form_submit_button("Next: Set Up Security Questions →")

                    if submitted_step1:
                        errors = []
                        if not name.replace(" ", "").isalpha():
                            errors.append("Name must contain only alphabets.")
                        if not identifier.isdigit():
                            errors.append("Matric Number must contain only numbers.")
                        if not department.replace(" ", "").isalpha():
                            errors.append("Department must contain only alphabets.")
                        if not name or not identifier or not password or not department:
                            errors.append("All fields are required.")
                        if errors:
                            for e in errors: st.error(e)
                        else:
                            # Check for duplicate matric before proceeding to step 2
                            _conn = get_db_connection()
                            _cur = _conn.cursor()
                            _cur.execute("SELECT id FROM users WHERE staff_id=%s AND role='student'", (identifier,))
                            if _cur.fetchone():
                                st.error("This Matric Number is already registered.")
                            else:
                                st.session_state["reg_pending"] = {
                                    "name": name,
                                    "identifier": identifier,
                                    "department": department,
                                    "password": password,
                                }
                                st.session_state["reg_step"] = 2
                                _conn.close()
                                st.rerun()

                # ---- STEP 2: Security Questions ----
                elif st.session_state["reg_step"] == 2:
                    st.markdown("**Step 2 of 2 — Set Up Your Security Questions**")
                    st.info(
                        "You must answer all 10 questions below. These will be used "
                        "to verify your identity at random points during your examinations. "
                        "For this public demo, use fictional but memorable answers. "
                        "You will need to recall them during your examination sessions."
                    )

                    with st.form("reg_form_Student_step2", clear_on_submit=False):
                        answers = []
                        for i, q in enumerate(SECURITY_QUESTIONS):
                            st.markdown(f"**{i+1}. {q}**")
                            ans = st.text_input(
                                "Your Answer",
                                key=f"sq_ans_{i}",
                                autocomplete="off",
                                label_visibility="collapsed"
                            )
                            answers.append(ans)

                        col_back, col_submit = st.columns([1, 2])
                        with col_back:
                            go_back = st.form_submit_button("← Back")
                        with col_submit:
                            submitted_step2 = st.form_submit_button("Complete Registration ✓", type="primary")

                    if go_back:
                        st.session_state["reg_step"] = 1
                        st.rerun()

                    if submitted_step2:
                        # Blacklist of obviously invalid/throwaway answers
                        INVALID_ANSWERS = {
                            "none", "n/a", "na", "nil", "null", "nothing",
                            "no", "n", "-", "skip", "test", "idk", "unknown",
                            "nill", "nope", "never", "blank", "empty", "x",
                        }

                        validation_errors = []
                        for i, ans in enumerate(answers):
                            stripped = ans.strip()
                            q_num = i + 1
                            if stripped == "":
                                validation_errors.append(
                                    f"Question {q_num}: Answer cannot be left blank."
                                )
                            elif len(stripped) < 3:
                                validation_errors.append(
                                    f"Question {q_num}: Answer is too short — please provide a full, genuine answer."
                                )
                            elif stripped.lower() in INVALID_ANSWERS:
                                validation_errors.append(
                                    f"Question {q_num}: \"{stripped}\" is not accepted — please provide a genuine answer."
                                )
                            elif len(set(stripped.lower())) == 1:
                                # Catches "aaaa", "1111", "xxxx" etc.
                                validation_errors.append(
                                    f"Question {q_num}: Answer must be a real, meaningful response."
                                )

                        if validation_errors:
                            for err in validation_errors:
                                st.error(err)
                        else:
                            security_answers = [
                                {"question": SECURITY_QUESTIONS[i], "answer": answers[i]}
                                for i in range(len(SECURITY_QUESTIONS))
                            ]
                            pending = st.session_state["reg_pending"]
                            try:
                                register_user(
                                    pending["name"],
                                    pending["identifier"],
                                    pending["password"],
                                    pending["department"],
                                    security_answers=security_answers,
                                )
                                st.success("Account Created Successfully! Please switch to Login.")
                                # Reset registration state
                                st.session_state["reg_step"] = 1
                                st.session_state["reg_pending"] = {}
                            except Exception as e:
                                st.error(f"Error: {e}")
