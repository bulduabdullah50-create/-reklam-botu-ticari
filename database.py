import os

def init_db():
    pass

def ensure_user(uid, username):
    pass

def get_user(uid):
    return {
        "interval_seconds": 3,
        "loop_interval_seconds": 240,
        "inc_groups": 1,
        "inc_channels": 1
    }

def set_user(uid, **kwargs):
    pass

def get_user_sessions_db(uid):
    # Hesapları doğrudan Render Environment Variables'dan okuyoruz (Asla silinmez!)
    sessions = []
    for key, value in os.environ.items():
        if key.startswith(f"SESSION_{uid}_"):
            phone = key.replace(f"SESSION_{uid}_", "")
            sessions.append((f"{uid}_{phone}", value))
    return sessions

def save_session_db(uid, session_name, session_string):
    # Render env otomatik güncellenemediği için kullanıcıya bilgi vereceğiz
    print(f"LÜTFEN RENDER PANELİNE EKLE: Key=SESSION_{session_name}, Value={session_string}")

def delete_session_db(session_name):
    pass

def license_valid(uid):
    return True

def use_license(uid, code):
    return True, "Lisans aktif!"

def create_license(code, days):
    pass

def list_licenses():
    return []
