import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from config import load_local_env


class LocalEnvironmentTests(unittest.TestCase):
    def test_loads_known_settings_and_preserves_environment_overrides(self):
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text(
                "\ufeff# Development settings\nDEMO_MODE=true\n"
                "GEMINI_API_KEY='local value'\nGROQ_API_KEY='groq value'\nUNEXPECTED=ignored\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"DEMO_MODE": "false"}, clear=True):
                load_local_env(env_file)
                self.assertEqual(os.environ["DEMO_MODE"], "false")
                self.assertEqual(os.environ["GEMINI_API_KEY"], "local value")
                self.assertEqual(os.environ["GROQ_API_KEY"], "groq value")
                self.assertNotIn("UNEXPECTED", os.environ)

    def test_missing_file_is_allowed(self):
        with tempfile.TemporaryDirectory() as directory:
            load_local_env(Path(directory) / "missing.env")


if __name__ == "__main__":
    unittest.main()
