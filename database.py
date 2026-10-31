
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
            is_admin INTEGER DEFAULT 0,
            ad_message_id INTEGER,
            interval_seconds INTEGER DEFAULT 3600,
            enabled INTEGER DEFAULT 0
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

        CREATE TABLE IF NOT EXISTS approved_groups (
            user_id INTEGER NOT NULL,
            chat_id INTEGER NOT NULL,
            title TEXT,
            approved_by INTEGER,
            PRIMARY KEY (user_id, chat_id)
        );
        """)

        # Eski veritabanlarına yeni sütunları ekle.
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(users)").fetchall()
        }

        migrations = {
            "username": "TEXT",
            "api_id": "INTEGER",
            "api_hash": "TEXT",
            "phone": "TEXT",
            "session_string": "TEXT",
            "is_active": "INTEGER DEFAULT 0",
            "license_expiry": "TEXT",
            "is_admin": "INTEGER DEFAULT 0",
            "ad_message_id": "INTEGER",
            "interval_seconds": "INTEGER DEFAULT 3600",
            "enabled": "INTEGER DEFAULT 0",
        }

        for name, definition in migrations.items():
            if name not in columns:
                conn.execute(
                    f"ALTER TABLE users ADD COLUMN {name} {definition}"
                )

        conn.commit()


def add_user(user_id, username=None):
    with connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users(user_id, username) VALUES(?, ?)",
            (user_id, username)
        )
        if username is not None:
            conn.execute(
                "UPDATE users SET username=? WHERE user_id=?",
                (username, user_id)
            )
        conn.execute(
            "INSERT OR IGNORE INTO tasks(user_id) VALUES(?)",
            (user_id,)
        )


def ensure_user(user_id, username=None):
    add_user(user_id, username)


def get_user(user_id):
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE user_id=?",
            (user_id,)
        ).fetchone()


def set_user(user_id, **kwargs):
    allowed = {
        "username",
        "api_id",
        "api_hash",
        "phone",
        "session_string",
        "is_active",
        "license_expiry",
        "is_admin",
        "ad_message_id",
        "interval_seconds",
        "enabled",
    }

    fields = {
        key: value
        for key, value in kwargs.items()
        if key in allowed
    }

    if not fields:
        return

    ensure_user(user_id)

    with connect() as conn:
        sql = ", ".join(f"{key}=?" for key in fields)
        conn.execute(
            f"UPDATE users SET {sql} WHERE user_id=?",
            (*fields.values(), user_id)
        )


def save_session(user_id, api_id, api_hash, phone, session_string):
    ensure_user(user_id)
    set_user(
        user_id,
        api_id=api_id,
        api_hash=api_hash,
        phone=phone,
        session_string=session_string,
        is_active=1
    )


def approve_group(user_id, chat_id, title=None, approved_by=None):
    ensure_user(user_id)

    with connect() as conn:
        conn.execute("""
            INSERT INTO approved_groups
                (user_id, chat_id, title, approved_by)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, chat_id)
            DO UPDATE SET
                title=excluded.title,
                approved_by=excluded.approved_by
        """, (user_id, chat_id, title, approved_by))


def remove_group(user_id, chat_id):
    with connect() as conn:
        conn.execute(
            "DELETE FROM approved_groups WHERE user_id=? AND chat_id=?",
            (user_id, chat_id)
        )


def groups_for(user_id):
    with connect() as conn:
        return conn.execute(
            "SELECT chat_id, title, approved_by FROM approved_groups "
            "WHERE user_id=? ORDER BY title",
            (user_id,)
        ).fetchall()


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


def create_license(code, duration_days):
    with connect() as conn:
        conn.execute(
            "INSERT INTO licenses(code, duration_days) VALUES(?, ?)",
            (code, duration_days)
        )


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

    return True, f"Lisans aktifleştirildi. Bitiş: {expiry}"


def license_valid(user_id):
    user = get_user(user_id)

    if not user or not user["license_expiry"]:
        return False

    try:
        expiry = datetime.fromisoformat(user["license_expiry"])
        return expiry > datetime.now(timezone.utc)
    except (ValueError, TypeError):
        return False
