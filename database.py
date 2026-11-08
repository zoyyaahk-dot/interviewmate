"""Small SQLite-backed store for a self-contained local Interview Mate demo."""

import os
import re
import sqlite3
from datetime import date, datetime
from pathlib import Path
from threading import Lock


APP_DIR = Path(__file__).resolve().parent
_SCHEMA_LOCK = Lock()
_SCHEMA_READY = False


class SQLiteCursor:
    """Keep the small cursor API used by the Flask routes."""

    def __init__(self, cursor, dictionary=False):
        self._cursor = cursor
        self._dictionary = dictionary

    def execute(self, statement, parameters=None):
        # The existing routes use MySQL-style placeholders. Translate them here
        # so route code stays simple while SQLite remains the only database.
        statement = statement.replace("%s", "?")
        statement = re.sub(r"\bCURDATE\(\)", "date('now', 'localtime')", statement, flags=re.IGNORECASE)
        statement = re.sub(
            r"ON DUPLICATE KEY UPDATE\s+goal_count\s*=\s*VALUES\(goal_count\)",
            "ON CONFLICT(user_id, goal_date) DO UPDATE SET goal_count = excluded.goal_count",
            statement,
            flags=re.IGNORECASE,
        )
        statement = re.sub(r"\bNOT\s+is_done\b", "(1 - is_done)", statement, flags=re.IGNORECASE)
        values = tuple(parameters or ())
        values = tuple(value.isoformat() if isinstance(value, (date, datetime)) else value for value in values)
        self._cursor.execute(statement, values)
        return self

    def _convert(self, row):
        return dict(row) if self._dictionary and row is not None else row

    def fetchone(self):
        return self._convert(self._cursor.fetchone())

    def fetchall(self):
        rows = self._cursor.fetchall()
        return [self._convert(row) for row in rows]

    @property
    def lastrowid(self):
        return self._cursor.lastrowid

    @property
    def rowcount(self):
        return self._cursor.rowcount

    def close(self):
        self._cursor.close()


class SQLiteConnection:
    def __init__(self, connection):
        self._connection = connection

    def cursor(self, dictionary=False):
        return SQLiteCursor(self._connection.cursor(), dictionary=dictionary)

    def commit(self):
        self._connection.commit()

    def rollback(self):
        self._connection.rollback()

    def close(self):
        self._connection.close()


def _database_path():
    configured_path = os.getenv("INTERVIEW_MATE_DB_PATH", "interview_mate.sqlite3")
    path = Path(configured_path).expanduser()
    return path if path.is_absolute() else APP_DIR / path


def _initialize_schema(connection):
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL COLLATE NOCASE UNIQUE,
            password TEXT NOT NULL,
            phone TEXT,
            college TEXT,
            course TEXT,
            year TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
        );

        CREATE TABLE IF NOT EXISTS test_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            test_type TEXT NOT NULL,
            score INTEGER NOT NULL,
            total INTEGER NOT NULL,
            percentage REAL NOT NULL,
            taken_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_test_results_user_taken
            ON test_results(user_id, taken_at);

        CREATE TABLE IF NOT EXISTS daily_goals (
            user_id INTEGER NOT NULL,
            goal_date TEXT NOT NULL,
            goal_count INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY (user_id, goal_date),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS study_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            scheduled_for TEXT NOT NULL,
            is_done INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_study_tasks_user_date
            ON study_tasks(user_id, scheduled_for);

        CREATE TABLE IF NOT EXISTS subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            plan TEXT NOT NULL,
            amount REAL NOT NULL,
            razorpay_subscription_id TEXT NOT NULL UNIQUE,
            razorpay_payment_id TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        """
    )


def get_db_connection():
    """Open the local database and create tables on first use."""
    global _SCHEMA_READY
    path = _database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        with _SCHEMA_LOCK:
            if not _SCHEMA_READY:
                _initialize_schema(connection)
                _SCHEMA_READY = True
    except Exception:
        connection.close()
        raise
    return SQLiteConnection(connection)
