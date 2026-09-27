"""SQLite storage for isolated, disposable CBT demo workspaces."""

import os
import re
import secrets
import shutil
import sqlite3
import time
from contextlib import closing
from contextvars import ContextVar
from pathlib import Path

from werkzeug.security import generate_password_hash

from config import load_local_env

load_local_env()

DATABASE_PATH = Path(
    os.getenv("CBT_DB_PATH", str(Path(__file__).with_name("data") / "demo.sqlite3"))
).expanduser()
WORKSPACE_LIFETIME_SECONDS = 7 * 24 * 60 * 60
_WORKSPACE_CODE = re.compile(r"[0-9a-f]{24}\Z")
_active_workspace = ContextVar("active_demo_workspace", default=None)


def current_workspace_code():
    return _active_workspace.get()


def clear_workspace():
    _active_workspace.set(None)


def _workspace_path(code):
    if not isinstance(code, str) or not _WORKSPACE_CODE.fullmatch(code):
        raise ValueError("Invalid workspace code.")
    return DATABASE_PATH.parent / "workspaces" / f"{code}.sqlite3"


def _workspace_metadata(code):
    try:
        path = _workspace_path(code)
    except ValueError:
        return None
    if not path.is_file() or path.is_symlink():
        return None
    try:
        with closing(sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)) as connection:
            return connection.execute(
                "SELECT expires_at, admin_key_hash FROM workspace_meta WHERE id=1"
            ).fetchone()
    except sqlite3.Error:
        return None


def activate_workspace(code):
    """Select an unexpired private demo database for this request only."""
    clear_workspace()
    metadata = _workspace_metadata(code)
    if metadata is None or metadata[0] <= int(time.time()):
        return False
    _active_workspace.set(code)
    return True


def create_workspace():
    """Create a private seven-day demo; return its join code and admin key."""
    directory = DATABASE_PATH.parent / "workspaces"
    directory.mkdir(parents=True, exist_ok=True)
    admin_key = secrets.token_urlsafe(24)
    for _ in range(3):
        code = secrets.token_hex(12)
        path = _workspace_path(code)
        try:
            path.touch(exist_ok=False)
            break
        except FileExistsError:
            continue
    else:
        raise RuntimeError("Unable to create a unique demo workspace.")
    with closing(sqlite3.connect(path)) as connection:
        with connection:
            connection.execute(
                "CREATE TABLE workspace_meta ("
                "id INTEGER PRIMARY KEY CHECK (id=1), "
                "expires_at INTEGER NOT NULL, admin_key_hash TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT INTO workspace_meta (id, expires_at, admin_key_hash) VALUES (1, ?, ?)",
                (int(time.time()) + WORKSPACE_LIFETIME_SECONDS, generate_password_hash(admin_key)),
            )
    _active_workspace.set(code)
    setup_database()
    return code, admin_key


def workspace_admin_key_hash():
    code = current_workspace_code()
    metadata = _workspace_metadata(code) if code else None
    return metadata[1] if metadata else None


def workspace_expiry():
    code = current_workspace_code()
    metadata = _workspace_metadata(code) if code else None
    return metadata[0] if metadata else None


def question_upload_directory():
    directory = Path(__file__).with_name("uploads")
    code = current_workspace_code()
    return directory / code if code else directory


def cleanup_expired_workspaces():
    """Remove expired private demo files; never touch the legacy shared database."""
    directory = DATABASE_PATH.parent / "workspaces"
    if not directory.is_dir():
        return 0
    removed = 0
    for path in directory.glob("*.sqlite3"):
        code = path.stem
        if not _WORKSPACE_CODE.fullmatch(code) or path.is_symlink():
            continue
        metadata = _workspace_metadata(code)
        if metadata is None or metadata[0] > int(time.time()):
            continue
        try:
            path.unlink()
            for suffix in ("-wal", "-shm"):
                path.with_name(path.name + suffix).unlink(missing_ok=True)
            upload_dir = Path(__file__).with_name("uploads") / code
            if upload_dir.is_dir() and not upload_dir.is_symlink():
                shutil.rmtree(upload_dir)
            removed += 1
        except OSError:
            # An active connection can lock a file; access still expires immediately.
            continue
    return removed


class DemoCursor:
    """Accept the existing MySQL-style placeholders and dictionary cursors."""

    def __init__(self, cursor, dictionary=False):
        self._cursor = cursor
        self._dictionary = dictionary

    def execute(self, statement, parameters=()):
        self._cursor.execute(statement.replace("%s", "?"), parameters or ())
        return self

    def fetchone(self):
        row = self._cursor.fetchone()
        return dict(row) if row is not None and self._dictionary else row

    def fetchall(self):
        rows = self._cursor.fetchall()
        return [dict(row) for row in rows] if self._dictionary else rows

    def __getattr__(self, name):
        return getattr(self._cursor, name)


class DemoConnection(sqlite3.Connection):
    def cursor(self, dictionary=False):
        return DemoCursor(super().cursor(), dictionary=dictionary)


def get_db_connection():
    code = current_workspace_code()
    path = _workspace_path(code) if code else DATABASE_PATH
    if code and not path.is_file():
        raise FileNotFoundError("This demo workspace is no longer available.")
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(
        path,
        timeout=30,
        detect_types=sqlite3.PARSE_DECLTYPES,
        factory=DemoConnection,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


def setup_database():
    """Create the demo schema without a separate database server."""
    connection = get_db_connection()
    connection.execute("PRAGMA journal_mode = WAL")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            department TEXT,
            staff_id TEXT NOT NULL,
            password TEXT NOT NULL,
            role TEXT NOT NULL CHECK (role IN ('admin', 'student')),
            session_token TEXT UNIQUE,
            UNIQUE (staff_id, role)
        );

        CREATE TABLE IF NOT EXISTS tests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            duration INTEGER NOT NULL,
            created_by INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            max_students INTEGER DEFAULT 0,
            release_option TEXT DEFAULT 'immediate',
            visible INTEGER DEFAULT 0,
            show_correct_answers INTEGER DEFAULT 0,
            FOREIGN KEY (created_by) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            test_id INTEGER NOT NULL,
            question TEXT NOT NULL,
            option_a TEXT,
            option_b TEXT,
            option_c TEXT,
            option_d TEXT,
            correct_option TEXT,
            topic TEXT DEFAULT 'General',
            subtopic TEXT,
            image_path TEXT,
            FOREIGN KEY (test_id) REFERENCES tests(id)
        );

        CREATE TABLE IF NOT EXISTS results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            test_id INTEGER NOT NULL,
            score INTEGER DEFAULT 0,
            total INTEGER DEFAULT 0,
            date_taken TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'completed',
            start_time TIMESTAMP,
            saved_answers TEXT,
            malpractice_warnings INTEGER DEFAULT 0,
            sq_trigger_indices TEXT,
            sq_passed_triggers TEXT,
            sq_attempts TEXT,
            sq_challenge_started_at TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (test_id) REFERENCES tests(id)
        );

        CREATE TABLE IF NOT EXISTS security_questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            question TEXT NOT NULL,
            answer TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS malpractice_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            test_id INTEGER NOT NULL,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            description TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (test_id) REFERENCES tests(id)
        );

        CREATE INDEX IF NOT EXISTS idx_questions_test ON questions(test_id);
        CREATE INDEX IF NOT EXISTS idx_results_user_test ON results(user_id, test_id);
        CREATE INDEX IF NOT EXISTS idx_results_test_status ON results(test_id, status);
        """
    )
    columns = {row[1] for row in connection.execute("PRAGMA table_info(results)")}
    if "sq_challenge_started_at" not in columns:
        connection.execute("ALTER TABLE results ADD COLUMN sq_challenge_started_at TIMESTAMP")
        connection.commit()
    connection.close()
