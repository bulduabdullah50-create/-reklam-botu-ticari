import os
import sqlite3
from pathlib import Path
from contextlib import contextmanager

DATABASE_PATH = os.getenv(
    "DATABASE_PATH",
    "data/bot.sqlite3"
)

Path(DATABASE_PATH).parent.mkdir(
    parents=True,
    exist_ok=True
)


@contextmanager
def get_db():
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row

    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def init_db():
    with get_db() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS licenses (
            license_key TEXT PRIMARY KEY,
            duration_hours INTEGER NOT NULL,
            slots INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            expires_at TEXT,
            activated_by INTEGER,
            revoked INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            license_key TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            phone TEXT NOT NULL,
            session_blob TEXT,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS allowed_groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            chat_id TEXT NOT NULL,
            account_id INTEGER,
            consent_note TEXT,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS settings (
            user_id INTEGER PRIMARY KEY,
            message_interval INTEGER NOT NULL DEFAULT 60,
            cycle_wait INTEGER NOT NULL DEFAULT 3600
        );

        CREATE TABLE IF NOT EXISTS excluded_groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            chat_id TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """)


def create_license(key, duration_hours, slots=1):
    with get_db() as db:
        db.execute(
            """
            INSERT INTO licenses
            (license_key, duration_hours, slots)
            VALUES (?, ?, ?)
            """,
            (key, duration_hours, slots)
        )


def get_license(key):
    with get_db() as db:
        return db.execute(
            "SELECT * FROM licenses WHERE license_key = ?",
            (key,)
        ).fetchone()


def activate_license(key, user_id):
    with get_db() as db:
        license_data = db.execute(
            "SELECT * FROM licenses WHERE license_key = ?",
            (key,)
        ).fetchone()

        if not license_data or license_data["revoked"]:
            return False

        if license_data["activated_by"] is not None:
            return license_data["activated_by"] == user_id

        from datetime import datetime, timedelta, timezone

        expiry = datetime.now(timezone.utc) + timedelta(
            hours=license_data["duration_hours"]
        )

        db.execute(
            """
            UPDATE licenses
            SET activated_by = ?, expires_at = ?
            WHERE license_key = ?
            """,
            (user_id, expiry.isoformat(), key)
        )

        db.execute(
            """
            INSERT OR REPLACE INTO users (user_id, license_key)
            VALUES (?, ?)
            """,
            (user_id, key)
        )

        return True


def license_is_valid(user_id):
    from datetime import datetime, timezone

    with get_db() as db:
        user = db.execute(
            """
            SELECT l.*
            FROM users u
            JOIN licenses l ON l.license_key = u.license_key
            WHERE u.user_id = ?
            """,
            (user_id,)
        ).fetchone()

        if not user or user["revoked"] or not user["expires_at"]:
            return False

        expiry = datetime.fromisoformat(user["expires_at"])

        return expiry > datetime.now(timezone.utc)


def save_settings(user_id, message_interval, cycle_wait):
    with get_db() as db:
        db.execute(
            """
            INSERT INTO settings
            (user_id, message_interval, cycle_wait)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                message_interval = excluded.message_interval,
                cycle_wait = excluded.cycle_wait
            """,
            (user_id, message_interval, cycle_wait)
        )


def get_settings(user_id):
    with get_db() as db:
        result = db.execute(
            "SELECT * FROM settings WHERE user_id = ?",
            (user_id,)
        ).fetchone()

        if result:
            return dict(result)

        return {
            "user_id": user_id,
            "message_interval": 60,
            "cycle_wait": 3600
        }


def add_allowed_group(user_id, chat_id, account_id=None, consent_note=""):
    with get_db() as db:
        db.execute(
            """
            INSERT INTO allowed_groups
            (user_id, chat_id, account_id, consent_note)
            VALUES (?, ?, ?, ?)
            """,
            (user_id, str(chat_id), account_id, consent_note)
        )


def list_allowed_groups(user_id):
    with get_db() as db:
        rows = db.execute(
            """
            SELECT * FROM allowed_groups
            WHERE user_id = ? AND active = 1
            ORDER BY id DESC
            """,
            (user_id,)
        ).fetchall()

        return [dict(row) for row in rows]


def add_excluded_group(user_id, chat_id):
    with get_db() as db:
        db.execute(
            """
            INSERT INTO excluded_groups (user_id, chat_id)
            VALUES (?, ?)
            """,
            (user_id, str(chat_id))
        )


def list_excluded_groups(user_id):
    with get_db() as db:
        rows = db.execute(
            """
            SELECT chat_id FROM excluded_groups
            WHERE user_id = ?
            """,
            (user_id,)
        ).fetchall()

        return [row["chat_id"] for row in rows]
