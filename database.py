import os
import re
import datetime
import psycopg2
import psycopg2.extras

def get_db():
    # Render'da hangi değişken adını yaptıysan hepsini kontrol eder ve tırnakları tertemiz temizler
    raw_url = os.environ.get("DB_URL") or os.environ.get("DATABASE_URL") or ""
    clean_url = raw_url.strip().strip('"').strip("'").strip()
    conn = psycopg2.connect(clean_url)
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
                    uid BIGINT PRIMARY KEY,
                    username TEXT,
                    interval_seconds INTEGER DEFAULT 3,
                    loop_interval_seconds INTEGER DEFAULT 240,
                    is_running INTEGER DEFAULT 0,
                    custom_ad_text TEXT DEFAULT ''
                )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS sessions (
                    session_name TEXT PRIMARY KEY,
                    uid BIGINT,
                    phone TEXT,
                    session_string TEXT,
                    username TEXT DEFAULT '',
                    custom_text TEXT DEFAULT '',
                    delay_seconds INTEGER DEFAULT 3,
                    loop_seconds INTEGER DEFAULT 240
                )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS licenses (
                    code TEXT PRIMARY KEY,
                    duration_days INTEGER,
                    is_used INTEGER DEFAULT 0,
                    used_by BIGINT DEFAULT 0,
                    status TEXT DEFAULT 'active',
                    note TEXT DEFAULT 'İsimsiz'
                )''')
                
    c.execute('''CREATE TABLE IF NOT EXISTS user_licenses (
                    uid BIGINT PRIMARY KEY,
                    expires_at TIMESTAMP
                )''')
    c.execute('''CREATE TABLE IF NOT EXISTS account_blacklist (
                    session_name TEXT,
                    group_id TEXT,
                    PRIMARY KEY (session_name, group_id)
                )''')
    conn.commit()
    c.close()
    conn.close()

def ensure_user(uid, username):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT uid FROM users WHERE uid = %s", (uid,))
    if not c.fetchone():
        c.execute("INSERT INTO users (uid, username) VALUES (%s, %s)", (uid, username))
        conn.commit()
    c.close()
    conn.close()

def get_user(uid):
    conn = get_db()
    c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    c.execute("SELECT * FROM users WHERE uid = %s", (uid,))
    row = c.fetchone()
    c.close()
    conn.close()
    return dict(row) if row else {}

def set_user(uid, **kwargs):
    conn = get_db()
    c = conn.cursor()
    for key, val in kwargs.items():
        c.execute(f"UPDATE users SET {key} = %s WHERE uid = %s", (val, uid))
    conn.commit()
    c.close()
    conn.close()

def save_session_db(uid, session_name, phone, session_string, username=""):
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        INSERT INTO sessions (session_name, uid, phone, session_string, username) 
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (session_name) 
        DO UPDATE SET uid = EXCLUDED.uid, phone = EXCLUDED.phone, session_string = EXCLUDED.session_string, username = EXCLUDED.username
    """, (session_name, uid, phone, session_string, username))
    conn.commit()
    c.close()
    conn.close()

def get_user_sessions_db(uid):
    conn = get_db()
    c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    c.execute("SELECT session_name, phone, session_string FROM sessions WHERE uid = %s", (uid,))
    rows = c.fetchall()
    c.close()
    conn.close()
    return [(row["session_name"], row["phone"], row["session_string"]) for row in rows]

def get_account_username(session_name):
    conn = get_db()
    c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    c.execute("SELECT username FROM sessions WHERE session_name = %s", (session_name,))
    row = c.fetchone()
    c.close()
    conn.close()
    return row["username"] if row and row["username"] else ""

def delete_session_db(session_name):
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM sessions WHERE session_name = %s", (session_name,))
    c.execute("DELETE FROM account_blacklist WHERE session_name = %s", (session_name,))
    conn.commit()
    c.close()
    conn.close()

def set_account_custom_text(session_name, text):
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE sessions SET custom_text = %s WHERE session_name = %s", (text, session_name))
    conn.commit()
    c.close()
    conn.close()

def get_account_custom_text(session_name):
    conn = get_db()
    c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    c.execute("SELECT custom_text FROM sessions WHERE session_name = %s", (session_name,))
    row = c.fetchone()
    c.close()
    conn.close()
    return row["custom_text"] if row and row["custom_text"] else ""

def set_account_delay(session_name, delay):
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE sessions SET delay_seconds = %s WHERE session_name = %s", (delay, session_name))
    conn.commit()
    c.close()
    conn.close()

def get_account_delay(session_name):
    conn = get_db()
    c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    c.execute("SELECT delay_seconds FROM sessions WHERE session_name = %s", (session_name,))
    row = c.fetchone()
    c.close()
    conn.close()
    return row["delay_seconds"] if row and row["delay_seconds"] is not None else 3

def set_account_loop(session_name, loop_seconds):
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE sessions SET loop_seconds = %s WHERE session_name = %s", (loop_seconds, session_name))
    conn.commit()
    c.close()
    conn.close()

def get_account_loop(session_name):
    conn = get_db()
    c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    c.execute("SELECT loop_seconds FROM sessions WHERE session_name = %s", (session_name,))
    row = c.fetchone()
    c.close()
    conn.close()
    return row["loop_seconds"] if row and row["loop_seconds"] is not None else 240

def add_account_blacklist_db(session_name, group_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        INSERT INTO account_blacklist (session_name, group_id) VALUES (%s, %s)
        ON CONFLICT (session_name, group_id) DO NOTHING
    """, (session_name, group_id))
    conn.commit()
    c.close()
    conn.close()

def get_account_blacklist_db(session_name):
    conn = get_db()
    c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    c.execute("SELECT group_id FROM account_blacklist WHERE session_name = %s", (session_name,))
    rows = c.fetchall()
    c.close()
    conn.close()
    return [r["group_id"] for r in rows]

def create_license(code, days, note="İsimsiz"):
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        INSERT INTO licenses (code, duration_days, status, note) 
        VALUES (%s, %s, 'active', %s)
        ON CONFLICT (code) DO UPDATE SET duration_days = EXCLUDED.duration_days, status = 'active', note = EXCLUDED.note
    """, (code, days, note))
    conn.commit()
    c.close()
    conn.close()

def list_licenses():
    conn = get_db()
    c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    c.execute("SELECT code, duration_days, is_used, used_by, status, note FROM licenses WHERE status != 'revoked'")
    rows = c.fetchall()
    c.close()
    conn.close()
    return [dict(r) for r in rows]

def revoke_license(code):
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE licenses SET status = 'revoked' WHERE code = %s", (code,))
    conn.commit()
    c.close()
    conn.close()

def add_license_time_db(uid, days):
    conn = get_db()
    c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    c.execute("SELECT expires_at FROM user_licenses WHERE uid = %s", (uid,))
    row = c.fetchone()
    now = datetime.datetime.now()
    if row and row["expires_at"]:
        try:
            current_exp = row["expires_at"]
            if current_exp > now:
                new_exp = current_exp + datetime.timedelta(days=days)
            else:
                new_exp = now + datetime.timedelta(days=days)
        except:
            new_exp = now + datetime.timedelta(days=days)
    else:
        new_exp = now + datetime.timedelta(days=days)
    
    c.execute("""
        INSERT INTO user_licenses (uid, expires_at) VALUES (%s, %s)
        ON CONFLICT (uid) DO UPDATE SET expires_at = EXCLUDED.expires_at
    """, (uid, new_exp))
    conn.commit()
    c.close()
    conn.close()
    return new_exp

def use_license(uid, code):
    conn = get_db()
    c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    c.execute("SELECT duration_days, is_used, status FROM licenses WHERE code = %s", (code,))
    row = c.fetchone()
    if not row:
        c.close()
        conn.close()
        return False, "❌ Böyle bir KEY bulunamadı!"
    if row["is_used"] or row["status"] == 'revoked':
        c.close()
        conn.close()
        return False, "⚠️ Bu KEY daha önce kullanılmış veya iptal edilmiş!"
    
    days = row["duration_days"]
    c.execute("UPDATE licenses SET is_used = 1, used_by = %s WHERE code = %s", (uid, code))
    conn.commit()
    c.close()
    conn.close()
    
    add_license_time_db(uid, days)
    return True, f"✅ KEY başarıyla aktif edildi! Hesabınıza {days} gün eklendi."

def license_valid(uid, admin_id):
    if int(uid) == int(admin_id):
        return True
    conn = get_db()
    c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    c.execute("SELECT expires_at FROM user_licenses WHERE uid = %s", (uid,))
    row = c.fetchone()
    c.close()
    conn.close()
    if not row or not row["expires_at"]:
        return False
    try:
        return datetime.datetime.now() < row["expires_at"]
    except:
        return False

def get_user_expiry(uid):
    conn = get_db()
    c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    c.execute("SELECT expires_at FROM user_licenses WHERE uid = %s", (uid,))
    row = c.fetchone()
    c.close()
    conn.close()
    return str(row["expires_at"]) if row and row["expires_at"] else None

def get_all_users():
    conn = get_db()
    c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    c.execute("SELECT uid FROM users")
    rows = c.fetchall()
    c.close()
    conn.close()
    return [r["uid"] for r in rows]

def get_all_running_users():
    conn = get_db()
    c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    c.execute("SELECT uid FROM users WHERE is_running = 1")
    rows = c.fetchall()
    c.close()
    conn.close()
    return [r["uid"] for r in rows]
