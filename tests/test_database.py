import tempfile
import unittest
import json
import os
from pathlib import Path
from unittest.mock import patch

import pandas as pd

import db
from admin import auto_classify_topic, completed_test_results, process_smart_paste
from ai import AIUnavailable
from auth import DEMO_ADMIN_KEY, get_user_by_token, login_user, register_admin, register_user, verify_security_answer
from student import answer_review_rows, ensure_challenge_start, pending_challenge_trigger, released_score_history, submit_exam


class DemoDatabaseTests(unittest.TestCase):
    def test_local_classifier_matches_whole_words(self):
        self.assertEqual(auto_classify_topic("meaningless"), "General Knowledge")
        self.assertEqual(auto_classify_topic("What is the powerhouse of the cell?"), "Biology")

    def test_smart_paste_rejects_incomplete_options(self):
        count, errors = process_smart_paste(
            "What is 2 + 2?\nA. 3\nB. 4\nC. 5\nANSWER: B", 1, ""
        )
        self.assertEqual(count, 0)
        self.assertIn("A, B, C and D", errors[0])

    def test_smart_paste_does_not_partially_save_on_parse_error(self):
        draft = (
            "What is 2 + 2?\nA. 3\nB. 4\nC. 5\nD. 6\nANSWER: B\n\n"
            "Incomplete question?\nA. Yes\nB. No\nANSWER: A"
        )
        with patch("admin.get_db_connection") as connection:
            count, errors = process_smart_paste(draft, 1, "", use_ai=False)
        self.assertEqual(count, 0)
        self.assertEqual(len(errors), 1)
        connection.assert_not_called()

    def test_smart_paste_uses_local_classification_when_ai_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(db, "DATABASE_PATH", Path(directory) / "demo.sqlite3"):
                db.setup_database()
                connection = db.get_db_connection()
                cursor = connection.cursor()
                cursor.execute("INSERT INTO tests (title, duration) VALUES (%s, %s)", ("Science", 15))
                test_id = cursor.lastrowid
                connection.commit()
                connection.close()
                with patch.dict(os.environ, {"GEMINI_API_KEY": "demo-test-key"}):
                    with patch("admin.classify_questions", side_effect=AIUnavailable("offline")) as ai_call:
                        count, errors = process_smart_paste(
                            "What is the powerhouse of the cell?\nA. Nucleus\nB. Mitochondrion\nC. Wall\nD. Ribosome\nANSWER: B",
                            test_id,
                            "",
                        )
                self.assertEqual((count, errors), (1, []))
                ai_call.assert_called_once()
                connection = db.get_db_connection()
                row = connection.execute("SELECT topic FROM questions WHERE test_id=?", (test_id,)).fetchone()
                connection.close()
                self.assertEqual(row["topic"], "Biology")

    def test_accounts_questions_and_results_use_sqlite(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(db, "DATABASE_PATH", Path(directory) / "demo.sqlite3"):
                with patch.dict("os.environ", {"DEMO_MODE": "true"}):
                    db.setup_database()
                    db.setup_database()
                    register_admin("Demo Admin", "101", "test-password", DEMO_ADMIN_KEY)
                    register_user("Demo Student", "202", "test-password", "Demo", [])

                    admin = login_user("101", "test-password", "admin")
                    student = login_user("202", "test-password", "student")
                    self.assertEqual(get_user_by_token(student["session_token"])["id"], student["id"])

                    connection = db.get_db_connection()
                    cursor = connection.cursor()
                    cursor.execute(
                        "INSERT INTO tests (title, duration, created_by, visible) VALUES (%s, %s, %s, %s)",
                        ("Sample", 15, admin["id"], True),
                    )
                    test_id = cursor.lastrowid
                    cursor.execute(
                        "INSERT INTO questions (test_id, question, correct_option) VALUES (%s, %s, %s)",
                        (test_id, "What is 2 + 2?", "B"),
                    )
                    cursor.execute(
                        "INSERT INTO results (user_id, test_id, score, total, status, start_time) "
                        "VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP)",
                        (student["id"], test_id, 1, 1, "completed"),
                    )
                    connection.commit()

                    rows = pd.read_sql("SELECT title FROM tests", connection)
                    self.assertEqual(rows["title"].tolist(), ["Sample"])
                    details = connection.cursor(dictionary=True)
                    details.execute("SELECT score, total FROM results WHERE user_id=%s", (student["id"],))
                    self.assertEqual(details.fetchone(), {"score": 1, "total": 1})
                    connection.close()

                    imported, errors = process_smart_paste(
                        "What is 3 + 4?\nA. 5\nB. 7\nC. 8\nD. 9\nANSWER: B",
                        test_id,
                        "Arithmetic",
                    )
                    self.assertEqual((imported, errors), (1, []))

                    connection = db.get_db_connection()
                    cursor = connection.cursor(dictionary=True)
                    cursor.execute("SELECT id FROM questions WHERE test_id=%s ORDER BY id DESC LIMIT 1", (test_id,))
                    question_id = cursor.fetchone()["id"]
                    cursor.execute(
                        "INSERT INTO results (user_id, test_id, status, start_time, saved_answers) "
                        "VALUES (%s, %s, 'ongoing', CURRENT_TIMESTAMP, %s)",
                        (student["id"], test_id, json.dumps({str(question_id): "B"})),
                    )
                    result_id = cursor.lastrowid
                    connection.commit()
                    connection.close()
                    self.assertEqual(
                        submit_exam(test_id, result_id, db.get_db_connection(), student["id"]),
                        {"score": 1, "total": 2},
                    )
                    self.assertEqual(
                        submit_exam(test_id, result_id, db.get_db_connection(), student["id"]),
                        {"score": 1, "total": 2},
                    )
                    with self.assertRaisesRegex(ValueError, "does not belong"):
                        submit_exam(test_id, result_id, db.get_db_connection(), admin["id"])

                    connection = db.get_db_connection()
                    connection.execute(
                        "INSERT INTO results (user_id, test_id, status, score, total) "
                        "VALUES (?, ?, 'ongoing', 0, 0)",
                        (student["id"], test_id),
                    )
                    connection.commit()
                    self.assertEqual(len(completed_test_results(connection, test_id)), 2)
                    connection.close()

    def test_challenge_deadline_survives_a_new_connection(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(db, "DATABASE_PATH", Path(directory) / "demo.sqlite3"):
                db.setup_database()
                connection = db.get_db_connection()
                cursor = connection.cursor()
                cursor.execute("INSERT INTO users (name, staff_id, password, role) VALUES ('Demo', '1', 'hash', 'student')")
                user_id = cursor.lastrowid
                cursor.execute("INSERT INTO tests (title, duration) VALUES ('Test', 15)")
                test_id = cursor.lastrowid
                cursor.execute(
                    "INSERT INTO results (user_id, test_id, status) VALUES (%s, %s, 'ongoing')",
                    (user_id, test_id),
                )
                result_id = cursor.lastrowid
                connection.commit()
                first = ensure_challenge_start(connection, result_id, user_id)
                connection.close()
                connection = db.get_db_connection()
                self.assertEqual(ensure_challenge_start(connection, result_id, user_id), first)
                connection.close()

    def test_question_navigation_cannot_skip_pending_challenges(self):
        self.assertIsNone(pending_challenge_trigger([2, 8], set(), 1))
        self.assertEqual(pending_challenge_trigger([2, 8], set(), 9), 2)
        self.assertEqual(pending_challenge_trigger([2, 8], {2}, 9), 8)
        self.assertIsNone(pending_challenge_trigger([2, 8], {2, 8}, 9))

    def test_new_security_answers_are_hashed(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(db, "DATABASE_PATH", Path(directory) / "demo.sqlite3"):
                db.setup_database()
                register_user("Demo", "20", "test-password", "Demo", [{"question": "Sample?", "answer": " Blue "}])
                connection = db.get_db_connection()
                stored = connection.execute("SELECT answer FROM security_questions").fetchone()["answer"]
                connection.close()
                self.assertNotEqual(stored, "blue")
                self.assertTrue(verify_security_answer(stored, "BLUE"))
                self.assertFalse(verify_security_answer(stored, "red"))
                self.assertTrue(verify_security_answer("blue", "Blue"))

    def test_released_score_history_excludes_unreleased_results(self):
        records = pd.DataFrame([
            {"Paper": "Later", "score": 4, "total": 5, "date_taken": "2026-09-02", "release_option": "immediate"},
            {"Paper": "Private", "score": 1, "total": 5, "date_taken": "2026-09-03", "release_option": "do_not_release"},
            {"Paper": "Earlier", "score": 3, "total": 5, "date_taken": "2026-09-01", "release_option": "immediate"},
        ])
        history = released_score_history(records)
        self.assertEqual(history["Paper"].tolist(), ["Earlier", "Later"])
        self.assertEqual(history["percentage"].tolist(), [60.0, 80.0])

    def test_answer_review_marks_unanswered_questions(self):
        questions = [
            {"id": 2, "question": "First?", "correct_option": "B"},
            {"id": 3, "question": "Second?", "correct_option": "A"},
        ]
        self.assertEqual(answer_review_rows(questions, {"2": "C"}), [
            {"Question": "First?", "Your answer": "C", "Correct answer": "B"},
            {"Question": "Second?", "Your answer": "Not answered", "Correct answer": "A"},
        ])


if __name__ == "__main__":
    unittest.main()
