import os
import sqlite3
from datetime import datetime, timedelta, timezone

DB_NAME = os.getenv("DB_NAME", "bot_system.db")


def connect():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with connect() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            api_id INTEGER,
            api_hash TEXT,
            phone TEXT,
            session_string TEXT,
            is_active INTEGER DEFAULT 0,
            license_expiry TEXT,
            is_admin INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS licenses (
            code TEXT PRIMARY KEY,
            duration_days INTEGER NOT NULL,
            is_used INTEGER DEFAULT 0,
            used_by INTEGER
        );

        CREATE TABLE IF NOT EXISTS tasks (
            user_id INTEGER PRIMARY KEY,
            custom_message TEXT,
            target_groups TEXT,
            interval_minutes INTEGER DEFAULT 10
        );
        """)
        conn.commit()


def add_user(user_id, username=None):
    with connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users(user_id, username) VALUES(?, ?)",
            (user_id, username)
        )
        conn.execute(
            "UPDATE users SET username=? WHERE user_id=?",
            (username, user_id)
        )
        conn.execute(
            "INSERT OR IGNORE INTO tasks(user_id) VALUES(?)",
            (user_id,)
        )
        conn.commit()


def get_user(user_id):
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE user_id=?",
            (user_id,)
        ).fetchone()
        return tuple(row) if row else None


def save_session(user_id, api_id, api_hash, phone, session_string):
    with connect() as conn:
        conn.execute("""
            UPDATE users
            SET api_id=?, api_hash=?, phone=?,
                session_string=?, is_active=1
            WHERE user_id=?
        """, (api_id, api_hash, phone, session_string, user_id))
        conn.commit()


def get_task(user_id):
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM tasks WHERE user_id=?",
            (user_id,)
        ).fetchone()
        return dict(row) if row else None


def update_task(user_id, **kwargs):
    allowed = {
        "custom_message",
        "target_groups",
        "interval_minutes"
    }

    fields = {
        key: value
        for key, value in kwargs.items()
        if key in allowed
    }

    if not fields:
        return

    with connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO tasks(user_id) VALUES(?)",
            (user_id,)
        )

        sql = ", ".join(f"{key}=?" for key in fields)

        conn.execute(
            f"UPDATE tasks SET {sql} WHERE user_id=?",
            (*fields.values(), user_id)
        )
        conn.commit()


def create_license(code, duration_days):
    with connect() as conn:
        conn.execute(
            "INSERT INTO licenses(code, duration_days) VALUES(?, ?)",
            (code, duration_days)
        )
        conn.commit()


def use_license(user_id, code):
    now = datetime.now(timezone.utc)

    with connect() as conn:
        lic = conn.execute(
            "SELECT duration_days, is_used FROM licenses WHERE code=?",
            (code,)
        ).fetchone()

        if not lic or lic["is_used"]:
            return False, "Geçersiz veya kullanılmış lisans!"

        user = conn.execute(
            "SELECT license_expiry FROM users WHERE user_id=?",
            (user_id,)
        ).fetchone()

        base = now

        if user and user["license_expiry"]:
            try:
                old = datetime.fromisoformat(user["license_expiry"])
                if old > now:
                    base = old
            except ValueError:
                pass

        expiry = (
            base + timedelta(days=int(lic["duration_days"]))
        ).isoformat()

        conn.execute(
            "UPDATE licenses SET is_used=1, used_by=? WHERE code=?",
            (user_id, code)
        )

        conn.execute(
            "UPDATE users SET license_expiry=? WHERE user_id=?",
            (expiry, user_id)
        )

        conn.commit()

    return True, f"Lisans aktifleştirildi. Bitiş: {expiry}"


def license_valid(user_id):
    user = get_user(user_id)

    if not user or not user[7]:
        return False

    try:
        expiry = datetime.fromisoformat(user[7])
        return expiry > datetime.now(timezone.utc)
    except ValueError:
        return False
