import os
from telethon import TelegramClient, events

# Railway değişkenlerinden bilgileri çekiyoruz
api_id = int(os.environ.get('API_ID', 0))
api_hash = os.environ.get('API_HASH', '')
bot_token = os.environ.get('BOT_TOKEN', '')

bot = TelegramClient('bot', api_id, api_hash).start(bot_token=bot_token)

@bot.on(events.NewMessage(pattern='/start'))
async def start(event):
    await event.respond('Bot aktif! Reklam komutlarını bekliyorum.')

print("Bot aktif edildi...")
bot.run_until_disconnected()
