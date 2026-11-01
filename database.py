import sqlite3

DB_NAME = "database.sqlite"

def get_db():
    conn = sqlite3.connect(DB_NAME, timeout=15)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
                    uid INTEGER PRIMARY KEY,
                    username TEXT,
                    interval_seconds INTEGER DEFAULT 3,
                    loop_interval_seconds INTEGER DEFAULT 240,
                    is_running INTEGER DEFAULT 0,
                    custom_ad_text TEXT DEFAULT ''
                )''')
    c.execute('''CREATE TABLE IF NOT EXISTS sessions (
                    session_name TEXT PRIMARY KEY,
                    uid INTEGER,
                    phone TEXT,
                    session_string TEXT
                )''')
    c.execute('''CREATE TABLE IF NOT EXISTS licenses (
                    code TEXT PRIMARY KEY,
                    duration_days INTEGER,
                    is_used INTEGER DEFAULT 0,
                    used_by INTEGER DEFAULT 0
                )''')
    c.execute('''CREATE TABLE IF NOT EXISTS user_licenses (
                    uid INTEGER PRIMARY KEY,
                    expires_at TIMESTAMP
                )''')
    c.execute('''CREATE TABLE IF NOT EXISTS account_blacklist (
                    session_name TEXT,
                    group_id TEXT,
                    PRIMARY KEY (session_name, group_id)
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

def save_session_db(uid, session_name, phone, session_string):
    conn = get_db()
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO sessions (session_name, uid, phone, session_string) VALUES (?, ?, ?, ?)", 
              (session_name, uid, phone, session_string))
    conn.commit()
    conn.close()

def get_user_sessions_db(uid):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT session_name, phone, session_string FROM sessions WHERE uid = ?", (uid,))
    rows = c.fetchall()
    conn.close()
    return [(row["session_name"], row["phone"], row["session_string"]) for row in rows]

def delete_session_db(session_name):
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM sessions WHERE session_name = ?", (session_name,))
    c.execute("DELETE FROM account_blacklist WHERE session_name = ?", (session_name,))
    conn.commit()
    conn.close()

def add_account_blacklist_db(session_name, group_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO account_blacklist (session_name, group_id) VALUES (?, ?)", (session_name, group_id))
    conn.commit()
    conn.close()

def get_account_blacklist_db(session_name):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT group_id FROM account_blacklist WHERE session_name = ?", (session_name,))
    rows = c.fetchall()
    conn.close()
    return [r["group_id"] for r in rows]

def create_license(code, days):
    conn = get_db()
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO licenses (code, duration_days) VALUES (?, ?)", (code, days))
    conn.commit()
    conn.close()

def list_licenses():
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT code, duration_days, is_used, used_by FROM licenses")
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def add_license_time_db(uid, days):
    import datetime
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT expires_at FROM user_licenses WHERE uid = ?", (uid,))
    row = c.fetchone()
    now = datetime.datetime.now()
    if row and row["expires_at"]:
        try:
            current_exp = datetime.datetime.strptime(row["expires_at"], "%Y-%m-%d %H:%M:%S.%f" if "." in row["expires_at"] else "%Y-%m-%d %H:%M:%S")
            if current_exp > now:
                new_exp = current_exp + datetime.timedelta(days=days)
            else:
                new_exp = now + datetime.timedelta(days=days)
        except:
            new_exp = now + datetime.timedelta(days=days)
    else:
        new_exp = now + datetime.timedelta(days=days)
    
    c.execute("INSERT OR REPLACE INTO user_licenses (uid, expires_at) VALUES (?, ?)", (uid, str(new_exp)))
    conn.commit()
    conn.close()
    return new_exp

def use_license(uid, code):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT duration_days, is_used FROM licenses WHERE code = ?", (code,))
    row = c.fetchone()
    if not row:
        conn.close()
        return False, "❌ Böyle bir KEY bulunamadı!"
    if row["is_used"]:
        conn.close()
        return False, "⚠️ Bu KEY daha önce kullanılmış!"
    
    days = row["duration_days"]
    c.execute("UPDATE licenses SET is_used = 1, used_by = ? WHERE code = ?", (uid, code))
    conn.commit()
    conn.close()
    
    add_license_time_db(uid, days)
    return True, f"✅ KEY başarıyla aktif edildi! Hesabınıza {days} gün eklendi."

def license_valid(uid, admin_id):
    if int(uid) == int(admin_id):
        return True
    import datetime
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT expires_at FROM user_licenses WHERE uid = ?", (uid,))
    row = c.fetchone()
    conn.close()
    if not row or not row["expires_at"]:
        return False
    try:
        expiry = datetime.datetime.strptime(row["expires_at"], "%Y-%m-%d %H:%M:%S.%f" if "." in row["expires_at"] else "%Y-%m-%d %H:%M:%S")
        return datetime.datetime.now() < expiry
    except:
        return False

def get_user_expiry(uid):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT expires_at FROM user_licenses WHERE uid = ?", (uid,))
    row = c.fetchone()
    conn.close()
    return row["expires_at"] if row and row["expires_at"] else None

def get_all_users():
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT uid FROM users")
    rows = c.fetchall()
    conn.close()
    return [r["uid"] for r in rows]
