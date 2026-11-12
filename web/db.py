"""Database connection and initialization module for SQLite."""

import os
import sqlite3
import secrets
from datetime import datetime
from flask import g, current_app


def get_db_path() -> str:
    """Return configured database path."""
    return current_app.config.get("DB_PATH")


def get_db() -> sqlite3.Connection:
    """Open a database connection for the current application context."""
    if "db" not in g:
        db_path = get_db_path()
        g.db = connect_db(db_path)
    return g.db


def connect_db(db_path: str) -> sqlite3.Connection:
    """Connect to SQLite database with secure permissions, WAL mode, and busy timeout."""
    if db_path != ":memory:":
        parent_dir = os.path.dirname(os.path.abspath(db_path))
        if not os.path.exists(parent_dir):
            os.makedirs(parent_dir, mode=0o700, exist_ok=True)
        try:
            os.chmod(parent_dir, 0o700)
        except OSError:
            pass

    conn = sqlite3.connect(db_path, timeout=10.0)
    conn.row_factory = sqlite3.Row

    # Ensure 0600 permissions on the SQLite database file
    if db_path != ":memory:" and os.path.exists(db_path):
        try:
            os.chmod(db_path, 0o600)
        except OSError:
            pass

    # Configure WAL mode and busy timeout
    if db_path != ":memory:":
        conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    conn.execute("PRAGMA foreign_keys=ON;")

    return conn


def close_db(e=None):
    """Close the database connection at the end of the request."""
    db = g.pop("db", None)
    if db is not None:
        db.close()


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS families (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    emails TEXT NOT NULL,
    mailing_address TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    members TEXT DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1,
    access_token TEXT NOT NULL UNIQUE,
    token_version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS wishlist_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    family_id INTEGER NOT NULL REFERENCES families(id) ON DELETE CASCADE,
    item_text TEXT NOT NULL,
    display_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS assignments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    year INTEGER NOT NULL,
    giver_family_id INTEGER NOT NULL REFERENCES families(id) ON DELETE CASCADE,
    receiver_family_id INTEGER NOT NULL REFERENCES families(id) ON DELETE CASCADE,
    published INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    published_at TEXT,
    UNIQUE(year, giver_family_id),
    UNIQUE(year, receiver_family_id)
);

CREATE TABLE IF NOT EXISTS email_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    year INTEGER NOT NULL,
    family_id INTEGER NOT NULL REFERENCES families(id) ON DELETE CASCADE,
    recipient_email TEXT NOT NULL,
    status TEXT NOT NULL,
    error_message TEXT DEFAULT '',
    sent_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS login_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ip_address TEXT NOT NULL,
    attempt_time TEXT NOT NULL,
    success INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_assignments_year ON assignments(year);
CREATE INDEX IF NOT EXISTS idx_families_token ON families(access_token);
CREATE INDEX IF NOT EXISTS idx_wishlist_family ON wishlist_items(family_id);
CREATE INDEX IF NOT EXISTS idx_login_attempts_ip ON login_attempts(ip_address, attempt_time);
"""


def init_db(db_path: str):
    """Initialize database tables and default settings."""
    conn = connect_db(db_path)
    with conn:
        conn.executescript(SCHEMA_SQL)

        # Default settings initialization
        current_year = str(datetime.now().year)
        defaults = {
            "current_year": current_year,
            "cycle_anchor_year": current_year,
            "assignment_mode": "randomized_cycle",
            "cycle_secret": secrets.token_hex(32),
            "no_repeat_years": "1",
            "roster_anchor_hash": "",
        }

        for key, val in defaults.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                (key, val)
            )
    conn.close()
