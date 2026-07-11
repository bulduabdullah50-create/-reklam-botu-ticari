import os
from telethon import TelegramClient

# Bot token'ı al
bot_token = os.getenv("BOT_TOKEN")

# Botu sadece token ile başlat (API_ID ve HASH'i sil!)
client = TelegramClient('bot', 38225321, '48499b759e2281784608038c660df2d').start(bot_token=bot_token)

print("Bot aktif!")
client.run_until_disconnected()
