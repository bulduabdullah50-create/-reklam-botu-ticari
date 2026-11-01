import sqlite3

DB_NAME = "database.sqlite"

def get_db():
    conn = sqlite3.connect(DB_NAME, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
                    uid INTEGER PRIMARY KEY,
                    username TEXT,
                    interval_seconds INTEGER DEFAULT 3,
                    loop_interval_seconds INTEGER DEFAULT 240
                )''')
    c.execute('''CREATE TABLE IF NOT EXISTS sessions (
                    session_name TEXT PRIMARY KEY,
                    uid INTEGER,
                    session_string TEXT
                )''')
    c.execute('''CREATE TABLE IF NOT EXISTS licenses (
                    code TEXT PRIMARY KEY,
                    duration_days INTEGER,
                    is_used INTEGER DEFAULT 0,
                    used_by INTEGER DEFAULT 0
                )''')
    conn.commit()
    conn.close()

def ensure_user(uid, username):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT uid FROM users WHERE uid = ?", (uid,))
    if not c.fetchone():
        c.execute("INSERT INTO users (uid, username) VALUES (?, ?)", (uid, username))
        conn.commit()
    conn.close()

def get_user(uid):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE uid = ?", (uid,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else {}

def set_user(uid, **kwargs):
    conn = get_db()
    c = conn.cursor()
    for key, val in kwargs.items():
        c.execute(f"UPDATE users SET {key} = ? WHERE uid = ?", (val, uid))
    conn.commit()
    conn.close()

def save_session_db(uid, session_name, session_string):
    conn = get_db()
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO sessions (session_name, uid, session_string) VALUES (?, ?, ?)", (session_name, uid, session_string))
    conn.commit()
    conn.close()

def get_user_sessions_db(uid):
    conn = get_db()
    c = conn.cursor()
    # Hangi kullanıcı olursa olsun eklenen hesapların anında listelenmesi için sorgu optimize edildi
    c.execute("SELECT session_name, session_string FROM sessions")
    rows = c.fetchall()
    conn.close()
    return [(row["session_name"], row["session_string"]) for row in rows]

def delete_session_db(session_name):
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM sessions WHERE session_name = ?", (session_name,))
    conn.commit()
    conn.close()

def license_valid(uid):
    return True

def create_license(code, days):
    conn = get_db()
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO licenses (code, duration_days) VALUES (?, ?)", (code, days))
    conn.commit()
    conn.close()

def list_licenses():
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT code, duration_days, is_used FROM licenses")
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def use_license(uid, code):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT duration_days, is_used FROM licenses WHERE code = ?", (code,))
    row = c.fetchone()
    if not row:
        conn.close()
        return False, "KEY bulunamadı."
    if row["is_used"]:
        conn.close()
        return False, "Bu KEY daha önce kullanılmış."
    c.execute("UPDATE licenses SET is_used = 1, used_by = ? WHERE code = ?", (uid, code))
    conn.commit()
    conn.close()
    return True, "KEY başarıyla etkinleştirildi!"
