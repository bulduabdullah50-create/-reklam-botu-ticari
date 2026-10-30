import os
from telethon import TelegramClient

# Render'a girdiğin BOT_TOKEN'ı direkt okur
bot_token = os.getenv("BOT_TOKEN")

# Telethon'u sadece bot token ile başlatıyoruz
client = TelegramClient('bot', 38225321, '48499b759e2281784608038c660df2d').start(bot_token=bot_token)

print("Bot aktif!")
client.run_until_disconnected()

