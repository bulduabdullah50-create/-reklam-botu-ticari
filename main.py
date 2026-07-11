import os
from telethon import TelegramClient

# Railway Variables kısmındaki isimlerle eşleşmeli
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")

# Botu sadece token ile başlatmayı dene
bot = TelegramClient('reklamci_ticari_session', API_ID, API_HASH)
bot.start(bot_token=BOT_TOKEN)

print("Bot başarıyla başlatıldı!")
bot.run_until_disconnected()
