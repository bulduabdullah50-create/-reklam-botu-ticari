import os
import sqlite3
from pathlib import Path

DB_PATH = Path(os.getenv("DATABASE_PATH", "data/bot.db"))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def connect():
    db = sqlite3.connect(DB_PATH, timeout=30)
    db.row_factory = sqlite3.Row
    return db


def init_db():
    with connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            telegram_id INTEGER PRIMARY KEY,
            session TEXT,
            api_id INTEGER,
            api_hash TEXT,
            ad_text TEXT DEFAULT '',
            interval INTEGER DEFAULT 3600,
            enabled INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS approved_groups (
            user_id INTEGER NOT NULL,
            chat_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            PRIMARY KEY(user_id, chat_id)
        );
        """)


def get_user(user_id):
    with connect() as db:
        row = db.execute(
            "SELECT * FROM users WHERE telegram_id=?",
            (user_id,)
        ).fetchone()
        return dict(row) if row else None


def create_user(user_id):
    with connect() as db:
        db.execute(
            "INSERT OR IGNORE INTO users(telegram_id) VALUES(?)",
            (user_id,)
        )


def update_user(user_id, **values):
    allowed = {
        "session", "api_id", "api_hash",
        "ad_text", "interval", "enabled"
    }
    values = {k: v for k, v in values.items() if k in allowed}
    if not values:
        return

    columns = ", ".join(f"{k}=?" for k in values)
    with connect() as db:
        db.execute(
            f"UPDATE users SET {columns} WHERE telegram_id=?",
            (*values.values(), user_id)
        )


def add_group(user_id, chat_id, title):
    with connect() as db:
        db.execute(
            """INSERT OR REPLACE INTO approved_groups
            (user_id, chat_id, title) VALUES (?, ?, ?)""",
            (user_id, chat_id, title)
        )


def remove_group(user_id, chat_id):
    with connect() as db:
        db.execute(
            "DELETE FROM approved_groups WHERE user_id=? AND chat_id=?",
            (user_id, chat_id)
        )


def get_groups(user_id):
    with connect() as db:
        rows = db.execute(
            "SELECT * FROM approved_groups WHERE user_id=?",
            (user_id,)
        ).fetchall()
        return [dict(r) for r in rows]
