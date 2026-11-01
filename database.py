import sqlite3
from pathlib import Path

DB_PATH = Path("data/bot.db")

def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Kullanıcılar tablosu
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            uid INTEGER PRIMARY KEY,
            username TEXT,
            inc_groups INTEGER DEFAULT 1,
            inc_channels INTEGER DEFAULT 1,
            interval_seconds INTEGER DEFAULT 3,
            loop_interval_seconds INTEGER DEFAULT 240,
            license_expires TIMESTAMP
        )
    """)
    
    # Lisans / KEY tablosu
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS licenses (
            code TEXT PRIMARY KEY,
            duration_days INTEGER,
            is_used INTEGER DEFAULT 0,
            used_by INTEGER
        )
    """)

    # --- KESİN ÇÖZÜM: Session'ları kalıcı veritabanında saklıyoruz ---
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            uid INTEGER,
            session_name TEXT PRIMARY KEY,
            session_string TEXT
        )
    """)
    
    conn.commit()
    conn.close()

def get_user(uid):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE uid = ?", (uid,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def ensure_user(uid, username):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO users (uid, username) VALUES (?, ?)", (uid, username))
    conn.commit()
    conn.close()

def set_user(uid, **kwargs):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    for key, val in kwargs.items():
        cursor.execute(f"UPDATE users SET {key} = ? WHERE uid = ?", (val, uid))
    conn.commit()
    conn.close()

def license_valid(uid):
    if int(uid) == 7824143216: # Admin kontrolü için
        return True
    user = get_user(uid)
    if not user or not user.get("license_expires"):
        return False
    from datetime import datetime
    exp = datetime.fromisoformat(user["license_expires"])
    return exp > datetime.now()

def create_license(code, days):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO licenses (code, duration_days, is_used) VALUES (?, ?, 0)", (code, days))
    conn.commit()
    conn.close()

def list_licenses():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM licenses")
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows

def use_license(uid, code):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM licenses WHERE code = ? AND is_used = 0", (code,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return False, "Geçersiz veya daha önce kullanılmış KEY!"
    
    days = row[1]
    cursor.execute("UPDATE licenses SET is_used = 1, used_by = ? WHERE code = ?", (uid, code))
    
    user = get_user(uid)
    from datetime import datetime, timedelta
    base = datetime.now()
    if user and user.get("license_expires"):
        current_exp = datetime.fromisoformat(user["license_expires"])
        if current_exp > base:
            base = current_exp
            
    new_exp = (base + timedelta(days=days)).isoformat()
    cursor.execute("UPDATE users SET license_expires = ? WHERE uid = ?", (new_exp, uid))
    conn.commit()
    conn.close()
    return True, f"KEY başarıyla aktif edildi! Süre: {days} gün uzatıldı."

# --- Session Veritabanı Fonksiyonları ---
def save_session_db(uid, session_name, session_string):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO sessions (uid, session_name, session_string) VALUES (?, ?, ?)", (uid, session_name, session_string))
    conn.commit()
    conn.close()

def get_user_sessions_db(uid):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT session_name, session_string FROM sessions WHERE uid = ?", (uid,))
    rows = cursor.fetchall()
    conn.close()
    return rows

def delete_session_db(session_name):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM sessions WHERE session_name = ?", (session_name,))
    conn.commit()
    conn.close()
