import os
from telethon import TelegramClient, events
# Railway'den gelen bilgileri temiz (strip) bir şekilde al
API_ID = int(os.getenv("API_ID").strip())
API_HASH = os.getenv("API_HASH").strip()
BOT_TOKEN = os.getenv("BOT_TOKEN").strip()
# Botu başlat
bot = TelegramClient('reklamci_ticari_session', API_ID, API_HASH).start(bot_token=BOT_TOKEN)
@bot.on(events.NewMessage(pattern='/start'))
async def start(event):
    await event.respond("Bot aktif!")
print("Bot aktif ve sunucuda çalışıyor...")
bot.run_until_disconnected()
