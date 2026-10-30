import os
import sys
from pathlib import Path
from telethon.sync import TelegramClient

if len(sys.argv) != 2:
    raise SystemExit(
        "Kullanım: python auth.py TELEGRAM_USER_ID"
    )

user_id = int(sys.argv[1])
api_id = int(os.environ["API_ID"])
api_hash = os.environ["API_HASH"]

root = Path(
    os.getenv("DATA_DIR", "data")
) / "sessions"

root.mkdir(parents=True, exist_ok=True)

session_path = root / str(user_id)

client = TelegramClient(
    str(session_path),
    api_id,
    api_hash
)

client.start()

print("Telegram oturumu oluşturuldu.")
print("Oturum dosyasını kimseyle paylaşma.")
print("GitHub'a yükleme.")

client.disconnect()
