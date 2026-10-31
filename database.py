
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta, timezone

DB_PATH = Path("data/bot.db")
DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def connect():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                ad_message_id INTEGER,
                interval_seconds INTEGER NOT NULL DEFAULT 3600,
                enabled INTEGER NOT NULL DEFAULT 0,
                created_at TEXT
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS approved_groups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                title TEXT,
                approved_by INTEGER,
                created_at TEXT,
                UNIQUE(user_id, chat_id)
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS licenses (
                code TEXT PRIMARY KEY,
                duration_days INTEGER NOT NULL,
                is_used INTEGER NOT NULL DEFAULT 0,
                used_by INTEGER,
                created_at TEXT,
                expires_at TEXT
            )
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_groups_user
            ON approved_groups(user_id)
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_licenses_user
            ON licenses(used_by)
        """)

        conn.commit()


def ensure_user(user_id, username=None):
    now = datetime.now(timezone.utc).isoformat()

    with connect() as conn:
        conn.execute("""
            INSERT INTO users (user_id, username, created_at)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username = COALESCE(excluded.username, users.username)
        """, (int(user_id), username, now))

        conn.commit()


def get_user(user_id):
    ensure_user(user_id)

    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE user_id = ?",
            (int(user_id),)
        ).fetchone()

    return dict(row) if row else None


def set_user(user_id, **kwargs):
    allowed = {
        "username",
        "ad_message_id",
        "interval_seconds",
        "enabled",
    }

    values = {
        key: value
        for key, value in kwargs.items()
        if key in allowed
    }

    if not values:
        return

    ensure_user(user_id)

    columns = ", ".join(f"{key} = ?" for key in values)
    params = list(values.values()) + [int(user_id)]

    with connect() as conn:
        conn.execute(
            f"UPDATE users SET {columns} WHERE user_id = ?",
            params
        )
        conn.commit()


def approve_group(user_id, chat_id, title, approved_by=None):
    ensure_user(user_id)

    now = datetime.now(timezone.utc).isoformat()

    with connect() as conn:
        conn.execute("""
            INSERT INTO approved_groups
                (user_id, chat_id, title, approved_by, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id, chat_id) DO UPDATE SET
                title = excluded.title,
                approved_by = excluded.approved_by
        """, (
            int(user_id),
            int(chat_id),
            str(title),
            approved_by,
            now
        ))

        conn.commit()


def remove_group(user_id, chat_id):
    with connect() as conn:
        conn.execute("""
            DELETE FROM approved_groups
            WHERE user_id = ? AND chat_id = ?
        """, (int(user_id), int(chat_id)))

        conn.commit()


def groups_for(user_id):
    with connect() as conn:
        rows = conn.execute("""
            SELECT chat_id, title, approved_by, created_at
            FROM approved_groups
            WHERE user_id = ?
            ORDER BY title
        """, (int(user_id),)).fetchall()

    return [dict(row) for row in rows]


def create_license(code, duration_days):
    duration_days = int(duration_days)

    if duration_days < 1:
        raise ValueError("Lisans süresi en az 1 gün olmalı.")

    now = datetime.now(timezone.utc).isoformat()

    with connect() as conn:
        conn.execute("""
            INSERT INTO licenses
                (code, duration_days, is_used, created_at)
            VALUES (?, ?, 0, ?)
        """, (str(code), duration_days, now))

        conn.commit()


def list_licenses():
    with connect() as conn:
        rows = conn.execute("""
            SELECT code, duration_days, is_used, used_by,
                   created_at, expires_at
            FROM licenses
            ORDER BY created_at DESC
            LIMIT 100
        """).fetchall()

    return [dict(row) for row in rows]


def use_license(user_id, code):
    user_id = int(user_id)
    code = str(code).strip().upper()
    now = datetime.now(timezone.utc)

    ensure_user(user_id)

    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")

        license_row = conn.execute("""
            SELECT * FROM licenses
            WHERE code = ?
        """, (code,)).fetchone()

        if not license_row:
            conn.rollback()
            return False, "KEY bulunamadı."

        if license_row["is_used"]:
            conn.rollback()
            return False, "Bu KEY daha önce kullanılmış."

        expires = now + timedelta(
            days=int(license_row["duration_days"])
        )

        conn.execute("""
            UPDATE licenses
            SET is_used = 1,
                used_by = ?,
                expires_at = ?
            WHERE code = ? AND is_used = 0
        """, (
            user_id,
            expires.isoformat(),
            code
        ))

        conn.commit()

    return True, (
        f"{license_row['duration_days']} günlük lisans aktifleştirildi. "
        f"Bitiş: {expires.strftime('%d.%m.%Y %H:%M')} UTC"
    )


def license_valid(user_id):
    now = datetime.now(timezone.utc)

    with connect() as conn:
        row = conn.execute("""
            SELECT expires_at
            FROM licenses
            WHERE used_by = ?
              AND is_used = 1
            ORDER BY expires_at DESC
            LIMIT 1
        """, (int(user_id),)).fetchone()

    if not row or not row["expires_at"]:
        return False

    try:
        expires = datetime.fromisoformat(row["expires_at"])

        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)

        return now < expires

    except (ValueError, TypeError):
        return False
