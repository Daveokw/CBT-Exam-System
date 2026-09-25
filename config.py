"""Read local development settings without storing secrets in source control."""

import os
from pathlib import Path


SETTING_NAMES = frozenset(
    {"CBT_DB_PATH", "DEMO_MODE", "ADMIN_SECRET_KEY", "GEMINI_API_KEY", "GROQ_API_KEY"}
)


def load_local_env(path=None):
    """Load the simple KEY=VALUE settings used by this project if present."""
    env_path = Path(path) if path is not None else Path(__file__).with_name(".env")
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8-sig").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, separator, value = stripped.partition("=")
        key = key.strip()
        if separator and key in SETTING_NAMES:
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            os.environ.setdefault(key, value)


load_local_env()
