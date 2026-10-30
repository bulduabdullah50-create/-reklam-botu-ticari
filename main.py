import os
import asyncio
import logging
from pathlib import Path

from telethon import TelegramClient
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

import database as db

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("farketmezda")

BOT_TOKEN = os.environ["BOT_TOKEN"]
API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]

DATA_DIR = Path(os.getenv("DATA_DIR", "data"))
SESSIONS = DATA_DIR / "sessions"
SESSIONS.mkdir(parents=True, exist_ok=True)

clients = {}
tasks = {}


def session_path(user_id):
    return SESSIONS / str(user_id)


async def get_client(user_id):
    path = session_path(user_id)

    if not Path(str(path) + ".session").exists():
        return None

    client = clients.get(user_id)

    if client is None:
        client = TelegramClient(
            str(path),
            API_ID,
            API_HASH
        )

        await client.connect()

        if not await client.is_user_authorized():
            await client.disconnect()
            return None

        clients[user_id] = client

    return client


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    db.ensure_user(user_id)

    await update.effective_message.reply_text(
        "FARKETMEZDA ÇOK KULLANICILI PANEL\n\n"
        "/gruplar - Üye olduğun gruplar\n"
        "/izinli - İzinli grupların\n"
        "/reklam - Kayıtlı Mesajlar'daki son mesajı seç\n"
        "/aralik 3600 - Gönderim aralığı\n"
        "/baslat - Gönderimi başlat\n"
        "/durdur - Gönderimi durdur\n"
        "/durum - Hesap durumun\n\n"
        "Hesap bağlantısı için README kurulumunu uygula."
    )


async def gruplar(update, context):
    user_id = update.effective_user.id
    client = await get_client(user_id)

    if not client:
        await update.effective_message.reply_text(
            "Telegram oturumu bulunamadı. "
            "Önce auth.py ile oturum oluştur."
        )
        return

    dialogs = await client.get_dialogs()
    groups = [dialog for dialog in dialogs if dialog.is_group]

    if not groups:
        await update.effective_message.reply_text(
            "Grup bulunamadı."
        )
        return

    lines = [
        f"{dialog.name}\nID: {dialog.id}"
        for dialog in groups
    ]

    message = "\n\n".join(lines)

    for i in range(0, len(message), 3500):
        await update.effective_message.reply_text(
            message[i:i + 3500]
        )


async def reklam(update, context):
    user_id = update.effective_user.id
    client = await get_client(user_id)

    if not client:
        await update.effective_message.reply_text(
            "Önce Telegram hesabını bağla."
        )
        return

    messages = await client.get_messages(
        "me",
        limit=1
    )

    if not messages:
        await update.effective_message.reply_text(
            "Kayıtlı Mesajlar boş."
        )
        return

    db.set_user(
        user_id,
        ad_message_id=messages[0].id
    )

    await update.effective_message.reply_text(
        "Reklam olarak Kayıtlı Mesajlar'daki "
        "son mesaj seçildi."
    )


async def izinli(update, context):
    user_id = update.effective_user.id
    groups = db.groups_for(user_id)

    if not groups:
        await update.effective_message.reply_text(
            "Henüz izinli grup bulunmuyor."
        )
        return

    result = "\n".join(
        f"{group['title']} — {group['chat_id']}"
        for group in groups
    )

    await update.effective_message.reply_text(result)


async def aralik(update, context):
    user_id = update.effective_user.id

    try:
        seconds = int(context.args[0])

        if not 900 <= seconds <= 86400:
            raise ValueError

    except (IndexError, ValueError):
        await update.effective_message.reply_text(
            "Kullanım: /aralik 3600\n"
            "En az 900, en fazla 86400 saniye."
        )
        return

    db.set_user(
        user_id,
        interval_seconds=seconds
    )

    await update.effective_message.reply_text(
        f"Gönderim aralığı {seconds} saniye olarak kaydedildi."
    )


async def sender(app, user_id):
    try:
        while db.get_user(user_id)["enabled"]:
            user = db.get_user(user_id)
            groups = db.groups_for(user_id)
            client = await get_client(user_id)

            if not client:
                break

            message_id = user["ad_message_id"]

            if not message_id or not groups:
                break

            message = await client.get_messages(
                "me",
                ids=message_id
            )

            if not message:
                break

            for group in groups:
                if not db.get_user(user_id)["enabled"]:
                    break

                # Yalnızca yöneticinin izin verdiği hedefler.
                current_groups = db.groups_for(user_id)

                if not any(
                    g["chat_id"] == group["chat_id"]
                    for g in current_groups
                ):
                    continue

                try:
                    await client.forward_messages(
                        entity=group["chat_id"],
                        messages=message
                    )

                except Exception:
                    log.exception(
                        "Gönderim hatası: kullanıcı=%s grup=%s",
                        user_id,
                        group["chat_id"]
                    )

                await asyncio.sleep(3)

            await asyncio.sleep(
                user["interval_seconds"]
            )

    except asyncio.CancelledError:
        raise

    except Exception:
        log.exception(
            "Gönderim döngüsü hatası: %s",
            user_id
        )

    finally:
        db.set_user(user_id, enabled=0)
        tasks.pop(user_id, None)


async def baslat(update, context):
    user_id = update.effective_user.id
    user = db.get_user(user_id)

    client = await get_client(user_id)
    groups = db.groups_for(user_id)

    if not client:
        await update.effective_message.reply_text(
            "Önce kişisel Telegram hesabını bağla."
        )
        return

    if not user["ad_message_id"]:
        await update.effective_message.reply_text(
            "Önce /reklam komutunu kullan."
        )
        return

    if not groups:
        await update.effective_message.reply_text(
            "İzinli grup bulunamadı."
        )
        return

    task = tasks.get(user_id)

    if task and not task.done():
        await update.effective_message.reply_text(
            "Gönderim zaten çalışıyor."
        )
        return

    db.set_user(user_id, enabled=1)

    tasks[user_id] = asyncio.create_task(
        sender(context.application, user_id)
    )

    await update.effective_message.reply_text(
        "Gönderim başlatıldı."
    )


async def durdur(update, context):
    user_id = update.effective_user.id

    db.set_user(user_id, enabled=0)

    task = tasks.get(user_id)

    if task and not task.done():
        task.cancel()

    await update.effective_message.reply_text(
        "Gönderim durduruldu."
    )


async def durum(update, context):
    user_id = update.effective_user.id
    user = db.get_user(user_id)

    session_exists = Path(
        str(session_path(user_id)) + ".session"
    ).exists()

    await update.effective_message.reply_text(
        f"Oturum: {'Bağlı' if session_exists else 'Bağlı değil'}\n"
        f"İzinli grup: {len(db.groups_for(user_id))}\n"
        f"Reklam: {'Seçildi' if user['ad_message_id'] else 'Seçilmedi'}\n"
        f"Aralık: {user['interval_seconds']} saniye\n"
        f"Çalışıyor: {'Evet' if user['enabled'] else 'Hayır'}"
    )


async def izin_ver(update, context):
    chat = update.effective_chat
    user = update.effective_user

    if chat.type not in ("group", "supergroup"):
        await update.effective_message.reply_text(
            "Bu komutu izin verilecek grupta kullan."
        )
        return

    try:
        member = await context.bot.get_chat_member(
            chat.id,
            user.id
        )
    except Exception:
        await update.effective_message.reply_text(
            "Yönetici kontrolü yapılamadı."
        )
        return

    if member.status not in ("administrator", "creator"):
        await update.effective_message.reply_text(
            "Bu komutu yalnızca grup yöneticisi kullanabilir."
        )
        return

    try:
        target_user_id = int(context.args[0])
    except (IndexError, ValueError):
        await update.effective_message.reply_text(
            "Kullanım: /izin_ver KULLANICI_TELEGRAM_ID"
        )
        return

    db.approve_group(
        target_user_id,
        chat.id,
        chat.title or str(chat.id),
        user.id
    )

    await update.effective_message.reply_text(
        "Grup yöneticisi izni kaydedildi."
    )


async def izin_kaldir(update, context):
    chat = update.effective_chat
    user = update.effective_user

    if chat.type not in ("group", "supergroup"):
        await update.effective_message.reply_text(
            "Bu komutu grupta kullan."
        )
        return

    try:
        member = await context.bot.get_chat_member(
            chat.id,
            user.id
        )
    except Exception:
        await update.effective_message.reply_text(
            "Yönetici kontrolü yapılamadı."
        )
        return

    if member.status not in ("administrator", "creator"):
        await update.effective_message.reply_text(
            "Yalnızca grup yöneticisi izni kaldırabilir."
        )
        return

    try:
        target_user_id = int(context.args[0])
    except (IndexError, ValueError):
        await update.effective_message.reply_text(
            "Kullanım: /izin_kaldir KULLANICI_TELEGRAM_ID"
        )
        return

    db.remove_group(target_user_id, chat.id)

    await update.effective_message.reply_text(
        "Grup izni kaldırıldı."
    )


async def post_shutdown(app):
    for task in list(tasks.values()):
        task.cancel()

    for client in list(clients.values()):
        await client.disconnect()


def main():
    db.init_db()

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_shutdown(post_shutdown)
        .build()
    )

    handlers = [
        ("start", start),
        ("gruplar", gruplar),
        ("reklam", reklam),
        ("izinli", izinli),
        ("aralik", aralik),
        ("baslat", baslat),
        ("durdur", durdur),
        ("durum", durum),
        ("izin_ver", izin_ver),
        ("izin_kaldir", izin_kaldir),
    ]

    for command, function in handlers:
        app.add_handler(
            CommandHandler(command, function)
        )

    app.run_polling()


if __name__ == "__main__":
    main()
