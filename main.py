import os
import asyncio
import re
from telethon import TelegramClient, events

# Railway Variables kısmından çekecek
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")

bot = TelegramClient('reklamci_ticari_session', API_ID, API_HASH).start(bot_token=BOT_TOKEN)

@bot.on(events.NewMessage(pattern='/start'))
async def start(event):
    await event.respond("Bot aktif! Yardım için /help yazın.")

print("Bot aktif ve sunucuda çalışıyor...")
bot.run_until_disconnected()
