import os
import sqlite3
from pathlib import Path

DATA_DIR = Path(os.getenv("DATA_DIR", "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "farketmezda.sqlite3"


def connect():
    db = sqlite3.connect(DB_PATH, timeout=30)
    db.row_factory = sqlite3.Row
    return db


def init_db():
    with connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            interval_seconds INTEGER NOT NULL DEFAULT 3600,
            ad_message_id INTEGER,
            enabled INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS approved_groups (
            user_id INTEGER NOT NULL,
            chat_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            approved_by INTEGER NOT NULL,
            PRIMARY KEY(user_id, chat_id)
        );
        """)


def ensure_user(user_id):
    with connect() as db:
        db.execute(
            "INSERT OR IGNORE INTO users(user_id) VALUES(?)",
            (user_id,)
        )


def get_user(user_id):
    ensure_user(user_id)
    with connect() as db:
        row = db.execute(
            "SELECT * FROM users WHERE user_id=?",
            (user_id,)
        ).fetchone()
        return dict(row)


def set_user(user_id, **values):
    allowed = {
        "interval_seconds",
        "ad_message_id",
        "enabled"
    }
    values = {
        key: value
        for key, value in values.items()
        if key in allowed
    }

    if not values:
        return

    ensure_user(user_id)
    columns = ", ".join(
        f"{key}=?" for key in values
    )

    with connect() as db:
        db.execute(
            f"UPDATE users SET {columns} WHERE user_id=?",
            (*values.values(), user_id)
        )


def approve_group(user_id, chat_id, title, approved_by):
    with connect() as db:
        db.execute("""
            INSERT OR REPLACE INTO approved_groups
            (user_id, chat_id, title, approved_by)
            VALUES (?, ?, ?, ?)
        """, (user_id, chat_id, title, approved_by))


def remove_group(user_id, chat_id):
    with connect() as db:
        db.execute("""
            DELETE FROM approved_groups
            WHERE user_id=? AND chat_id=?
        """, (user_id, chat_id))


def groups_for(user_id):
    with connect() as db:
        rows = db.execute("""
            SELECT * FROM approved_groups
            WHERE user_id=?
            ORDER BY title
        """, (user_id,)).fetchall()

        return [dict(row) for row in rows]
