import os
import unittest
from unittest.mock import patch

from auth import DEMO_ADMIN_KEY, admin_registration_key, register_admin


class AdminRegistrationTests(unittest.TestCase):
    def test_missing_admin_key_cannot_register_an_administrator(self):
        with patch.dict(os.environ, {"DEMO_MODE": "false", "ADMIN_SECRET_KEY": ""}):
            with self.assertRaisesRegex(ValueError, "Invalid admin secret key"):
                register_admin("Demo Admin", "123", "password", "any value")

    def test_wrong_admin_key_is_rejected_before_database_access(self):
        with patch.dict(os.environ, {"DEMO_MODE": "false", "ADMIN_SECRET_KEY": "configured secret"}):
            with self.assertRaisesRegex(ValueError, "Invalid admin secret key"):
                register_admin("Demo Admin", "123", "password", "wrong secret")

    def test_demo_mode_shows_a_known_registration_key(self):
        with patch.dict(os.environ, {"DEMO_MODE": "true", "ADMIN_SECRET_KEY": "private secret"}):
            self.assertEqual(admin_registration_key(), DEMO_ADMIN_KEY)


if __name__ == "__main__":
    unittest.main()
