import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

import db


class DemoAppTests(unittest.TestCase):
    def test_public_demo_opens_and_shows_admin_key(self):
        app_path = Path(__file__).resolve().parents[1] / "app.py"
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(db, "DATABASE_PATH", Path(directory) / "demo.sqlite3"):
                app = AppTest.from_file(str(app_path)).run(timeout=25)
                self.assertFalse(app.exception)
                self.assertEqual(app.title[0].value, "CBT System")

                app.sidebar.radio[0].set_value("Register").run(timeout=25)
                app.radio[0].set_value("Admin").run(timeout=25)
                self.assertFalse(app.exception)
                self.assertTrue(any("DEMO-ADMIN" in info.value for info in app.info))


if __name__ == "__main__":
    unittest.main()
