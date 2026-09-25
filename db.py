"""SQLite storage for the disposable public CBT demonstration."""

import os
import sqlite3
from pathlib import Path

from config import load_local_env

load_local_env()

DATABASE_PATH = Path(
    os.getenv("CBT_DB_PATH", str(Path(__file__).with_name("data") / "demo.sqlite3"))
).expanduser()


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
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(
        DATABASE_PATH,
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
