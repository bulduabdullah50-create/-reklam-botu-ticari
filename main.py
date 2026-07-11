import os
from telethon import TelegramClient

# Railway Variables'tan çek
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")

# Botu token ile değil, kullanıcı olarak başlat
# Bu, kodun ilk çalıştığında senden telefon numarası ve kod isteyeceği anlamına gelir
bot = TelegramClient('reklamci_ticari_session', API_ID, API_HASH)

async def main():
    await bot.start()
    print("Bot başarıyla bağlandı!")
    await bot.run_until_disconnected()

import asyncio
asyncio.run(main())
