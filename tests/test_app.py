import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

import db
from auth import login_user, register_admin, register_user


class DemoAppTests(unittest.TestCase):
    def test_private_demo_opens_and_shows_creator_admin_key(self):
        app_path = Path(__file__).resolve().parents[1] / "app.py"
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(db, "DATABASE_PATH", Path(directory) / "demo.sqlite3"):
                app = AppTest.from_file(str(app_path)).run(timeout=25)
                self.assertFalse(app.exception)
                self.assertEqual(app.title[0].value, "CBT System")
                next(button for button in app.button if button.label == "Create private demo").click().run(timeout=25)
                self.assertFalse(app.exception)
                self.assertTrue(any("administrator key" in item.value for item in app.warning))
                app.sidebar.radio[0].set_value("Register").run(timeout=25)
                app.radio[0].set_value("Admin").run(timeout=25)
                self.assertFalse(app.exception)
                self.assertTrue(any("administrator key" in item.value for item in app.warning))

    def test_admin_can_sign_in_to_private_demo(self):
        app_path = Path(__file__).resolve().parents[1] / "app.py"
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(db, "DATABASE_PATH", Path(directory) / "demo.sqlite3"):
                code, admin_key = db.create_workspace()
                register_admin("Demo Admin", "101", "password", admin_key)
                db.clear_workspace()
                app = AppTest.from_file(str(app_path))
                app.session_state["workspace_code"] = code
                app.run(timeout=25)
                app.radio[0].set_value("Admin").run(timeout=25)
                next(item for item in app.text_input if item.label == "Staff ID (Numbers Only)").set_value("101")
                next(item for item in app.text_input if item.label == "Password").set_value("password")
                next(button for button in app.button if button.label == "Login").click().run(timeout=25)
                self.assertFalse(app.exception)
                self.assertTrue(any(item.value == "Admin Dashboard" for item in app.title))

    def test_question_palette_shows_first_question_and_navigates(self):
        app_path = Path(__file__).resolve().parents[1] / "app.py"
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(db, "DATABASE_PATH", Path(directory) / "demo.sqlite3"):
                code, _ = db.create_workspace()
                register_user("Demo Student", "202", "password", "Demo")
                user = login_user("202", "password", "student")
                with closing(db.get_db_connection()) as connection:
                    with connection:
                        test_id = connection.execute(
                            "INSERT INTO tests (title, duration, visible) VALUES ('Palette test', 15, 1)"
                        ).lastrowid
                        for index in (1, 2):
                            connection.execute(
                                "INSERT INTO questions (test_id, question, option_a, option_b, "
                                "option_c, option_d, correct_option) VALUES (?, ?, 'A', 'B', 'C', 'D', 'A')",
                                (test_id, f"Sample question {index}?"),
                            )
                        connection.execute(
                            "INSERT INTO results (user_id, test_id, status, start_time, "
                            "saved_answers, sq_trigger_indices) "
                            "VALUES (?, ?, 'ongoing', CURRENT_TIMESTAMP, '{}', '[]')",
                            (user["id"], test_id),
                        )
                db.clear_workspace()
                app = AppTest.from_file(str(app_path))
                app.session_state["workspace_code"] = code
                app.session_state["user"] = user
                app.session_state["role"] = "student"
                app.run(timeout=25)
                self.assertFalse(app.exception)
                labels = [button.label for button in app.sidebar.button]
                self.assertIn("1", labels)
                self.assertIn("2", labels)
                self.assertTrue(any(item.value == "Question 1 of 2" for item in app.subheader))
                next(button for button in app.sidebar.button if button.label == "2").click().run(timeout=25)
                self.assertFalse(app.exception)
                self.assertTrue(any(item.value == "Question 2 of 2" for item in app.subheader))


if __name__ == "__main__":
    unittest.main()
