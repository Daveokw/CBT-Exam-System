import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import db
from auth import login_user, register_admin, register_user


class PrivateWorkspaceTests(unittest.TestCase):
    def tearDown(self):
        db.clear_workspace()

    def test_workspaces_isolate_accounts_and_exams(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(db, "DATABASE_PATH", Path(directory) / "demo.sqlite3"):
                first_code, first_admin_key = db.create_workspace()
                register_admin("First Admin", "101", "password", first_admin_key)
                register_user("First Student", "202", "password", "Demo")
                with closing(db.get_db_connection()) as connection:
                    with connection:
                        connection.execute("INSERT INTO tests (title, duration) VALUES ('Private exam', 10)")

                second_code, second_admin_key = db.create_workspace()
                self.assertNotEqual(first_code, second_code)
                self.assertIsNone(login_user("202", "password", "student"))
                with closing(db.get_db_connection()) as connection:
                    self.assertEqual(connection.execute("SELECT COUNT(*) FROM tests").fetchone()[0], 0)
                with self.assertRaisesRegex(ValueError, "Invalid admin secret key"):
                    register_admin("Other Admin", "303", "password", first_admin_key)
                register_admin("Other Admin", "303", "password", second_admin_key)

                self.assertTrue(db.activate_workspace(first_code))
                self.assertIsNotNone(login_user("202", "password", "student"))
                with closing(db.get_db_connection()) as connection:
                    self.assertEqual(connection.execute("SELECT COUNT(*) FROM tests").fetchone()[0], 1)

    def test_expired_workspace_is_inaccessible_and_removed_without_touching_legacy_data(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(db, "DATABASE_PATH", Path(directory) / "demo.sqlite3"):
                db.clear_workspace()
                db.setup_database()
                legacy_path = db.DATABASE_PATH
                code, _ = db.create_workspace()
                path = legacy_path.parent / "workspaces" / f"{code}.sqlite3"
                with closing(sqlite3.connect(path)) as connection:
                    with connection:
                        connection.execute("UPDATE workspace_meta SET expires_at=1 WHERE id=1")
                self.assertFalse(db.activate_workspace(code))
                self.assertEqual(db.cleanup_expired_workspaces(), 1)
                self.assertFalse(path.exists())
                self.assertTrue(legacy_path.exists())

    def test_invalid_workspace_codes_cannot_select_other_files(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(db, "DATABASE_PATH", Path(directory) / "demo.sqlite3"):
                self.assertFalse(db.activate_workspace("../demo.sqlite3"))
                self.assertFalse(db.activate_workspace("missing"))
                self.assertIsNone(db.current_workspace_code())


if __name__ == "__main__":
    unittest.main()
