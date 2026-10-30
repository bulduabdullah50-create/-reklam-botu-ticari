import os
import random
import string
import logging

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    ConversationHandler,
    filters
)

import database as db
import userbot_manager as ub

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

(
    WAIT_LICENSE,
    WAIT_API_ID,
    WAIT_API_HASH,
    WAIT_PHONE,
    WAIT_OTP,
    WAIT_MSG,
    WAIT_GROUPS,
    WAIT_INTERVAL
) = range(8)

logging.basicConfig(level=logging.INFO)


def keyboard(user_id):
    rows = [
        [
            InlineKeyboardButton("🔗 Hesap bağla", callback_data="connect"),
            InlineKeyboardButton("🔑 Lisansım", callback_data="license")
        ],
        [
            InlineKeyboardButton("✉️ Mesaj ayarla", callback_data="msg"),
            InlineKeyboardButton("🎯 Grup seç", callback_data="groups")
        ],
        [
            InlineKeyboardButton("⏱ Aralık", callback_data="interval"),
            InlineKeyboardButton("📊 Durum", callback_data="status")
        ],
        [
            InlineKeyboardButton("🚀 Başlat", callback_data="run"),
            InlineKeyboardButton("⏹ Durdur", callback_data="stop")
        ],
        [
            InlineKeyboardButton("❓ Yardım", callback_data="help")
        ]
    ]

    if user_id == ADMIN_ID:
        rows.append([
            InlineKeyboardButton(
                "👑 Admin paneli",
                callback_data="admin"
            )
        ])

    return InlineKeyboardMarkup(rows)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db.add_user(user.id, user.username)

    await update.effective_message.reply_text(
        "✨ TELEGRAM CONTROL\n\nMenüden işlem seçin.",
        reply_markup=keyboard(user.id)
    )


async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    uid = query.from_user.id
    data = query.data

    db.add_user(uid, query.from_user.username)

    user = db.get_user(uid)
    task = db.get_task(uid) or {}

    if data == "home":
        await query.message.reply_text(
            "Ana Menü",
            reply_markup=keyboard(uid)
        )

    elif data == "license":
        expiry = user[7] if user and user[7] else "Lisans yok"

        await query.message.reply_text(
            f"Lisans bitiş tarihi: {expiry}",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "Lisans kodu gir",
                        callback_data="lic_enter"
                    )
                ]
            ])
        )

    elif data == "lic_enter":
        await query.message.reply_text("Lisans kodunu gönderin:")
        return WAIT_LICENSE

    elif data == "connect":
        await query.message.reply_text(
            "Telegram API ID bilginizi gönderin:"
        )
        return WAIT_API_ID

    elif data == "msg":
        await query.message.reply_text(
            "Paylaşılacak mesajı gönderin:"
        )
        return WAIT_MSG

    elif data == "groups":
        await query.message.reply_text(
            "Hedef grup kullanıcı adlarını veya ID'lerini "
            "virgülle ayırarak gönderin:"
        )
        return WAIT_GROUPS

    elif data == "interval":
        await query.message.reply_text(
            "Aralığı dakika olarak gönderin (en az 10):"
        )
        return WAIT_INTERVAL

    elif data == "status":
        await query.message.reply_text(
            f"Hesap: {'Bağlı' if user and user[5] else 'Bağlı değil'}\n"
            f"Lisans: {'Aktif' if db.license_valid(uid) else 'Aktif değil'}\n"
            f"Görev: {'Çalışıyor' if ub.is_running(uid) else 'Durdu'}"
        )

    elif data == "run":
        if not db.license_valid(uid):
            await query.message.reply_text(
                "Önce geçerli lisans girin."
            )
            return ConversationHandler.END

        if not user or not user[5] or not user[2] or not user[3]:
            await query.message.reply_text(
                "Önce Telegram hesabınızı bağlayın."
            )
            return ConversationHandler.END

        groups = [
            x.strip()
            for x in (task.get("target_groups") or "").split(",")
            if x.strip()
        ]

        ok, result = ub.start_userbot(
            uid,
            user[2],
            user[3],
            user[5],
            groups,
            task.get("custom_message") or "",
            task.get("interval_minutes") or 10
        )

        await query.message.reply_text(result)

    elif data == "stop":
        ok, result = await ub.stop_userbot(uid)
        await query.message.reply_text(result)

    elif data == "admin" and uid == ADMIN_ID:
        await query.message.reply_text(
            "Lisans üretmek için:\n/lisans 30\n/lisans 7"
        )

    elif data == "help":
        await query.message.reply_text(
            "Hesabınızı bağlayın, lisansınızı etkinleştirin, "
            "mesaj ve grup ayarlarını yapın."
        )

    return ConversationHandler.END


async def save_license(update, context):
    uid = update.effective_user.id

    success, result = db.use_license(
        uid,
        update.message.text.strip()
    )

    await update.message.reply_text(
        result,
        reply_markup=keyboard(uid)
    )

    return ConversationHandler.END


async def api_id_step(update, context):
    try:
        context.user_data["api_id"] = int(
            update.message.text.strip()
        )
    except ValueError:
        await update.message.reply_text(
            "API ID sayısal olmalıdır:"
        )
        return WAIT_API_ID

    await update.message.reply_text("API Hash gönderin:")
    return WAIT_API_HASH


async def api_hash_step(update, context):
    context.user_data["api_hash"] = update.message.text.strip()

    await update.message.reply_text(
        "Telefon numaranızı ülke koduyla gönderin:"
    )

    return WAIT_PHONE


async def phone_step(update, context):
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    phone = update.message.text.strip()

    client = TelegramClient(
        StringSession(),
        context.user_data["api_id"],
        context.user_data["api_hash"]
    )

    await client.connect()

    try:
        sent = await client.send_code_request(phone)

        context.user_data["pending_client"] = client
        context.user_data["phone"] = phone
        context.user_data["phone_code_hash"] = sent.phone_code_hash

        await update.message.reply_text(
            "Telegram giriş kodunu gönderin:"
        )

        return WAIT_OTP

    except Exception:
        await client.disconnect()
        raise


async def otp_step(update, context):
    from telethon.errors import SessionPasswordNeededError

    client = context.user_data.get("pending_client")

    if not client:
        await update.message.reply_text(
            "Oturum bulunamadı. Yeniden hesap bağlayın."
        )
        return ConversationHandler.END

    try:
        await client.sign_in(
            phone=context.user_data["phone"],
            code=update.message.text.strip(),
            phone_code_hash=context.user_data["phone_code_hash"]
        )

    except SessionPasswordNeededError:
        await client.disconnect()
        context.user_data.clear()

        await update.message.reply_text(
            "İki aşamalı doğrulama açık. "
            "Hesabınızı yerel güvenli oturum aracıyla bağlamanız gerekiyor."
        )
        return ConversationHandler.END

    uid = update.effective_user.id

    session_string = client.session.save()

    db.save_session(
        uid,
        context.user_data["api_id"],
        context.user_data["api_hash"],
        context.user_data["phone"],
        session_string
    )

    await client.disconnect()
    context.user_data.clear()

    await update.message.reply_text(
        "Telegram hesabı bağlandı.",
        reply_markup=keyboard(uid)
    )

    return ConversationHandler.END


async def save_msg(update, context):
    uid = update.effective_user.id

    db.update_task(
        uid,
        custom_message=update.message.text
    )

    await update.message.reply_text(
        "Mesaj kaydedildi.",
        reply_markup=keyboard(uid)
    )

    return ConversationHandler.END


async def save_groups(update, context):
    uid = update.effective_user.id

    db.update_task(
        uid,
        target_groups=update.message.text.strip()
    )

    await update.message.reply_text(
        "Grup listesi kaydedildi.",
        reply_markup=keyboard(uid)
    )

    return ConversationHandler.END


async def save_interval(update, context):
    uid = update.effective_user.id

    try:
        minutes = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text(
            "Dakika olarak sayı girin:"
        )
        return WAIT_INTERVAL

    if minutes < 10:
        await update.message.reply_text(
            "En az 10 dakika girin:"
        )
        return WAIT_INTERVAL

    db.update_task(
        uid,
        interval_minutes=minutes
    )

    await update.message.reply_text(
        "Aralık kaydedildi.",
        reply_markup=keyboard(uid)
    )

    return ConversationHandler.END


async def make_license(update, context):
    if update.effective_user.id != ADMIN_ID:
        return

    try:
        days = int(context.args[0])
    except (IndexError, ValueError):
        await update.message.reply_text(
            "Kullanım: /lisans 30"
        )
        return

    if not 1 <= days <= 3650:
        await update.message.reply_text(
            "Gün sayısı 1 ile 3650 arasında olmalı."
        )
        return

    code = "JANTI-" + "".join(
        random.choices(
            string.ascii_uppercase + string.digits,
            k=10
        )
    )

    db.create_license(code, days)

    await update.message.reply_text(
        f"Lisans: {code}\nSüre: {days} gün"
    )


def main():
    if not TOKEN:
        raise RuntimeError(
            "BOT_TOKEN .env dosyasında tanımlanmalı."
        )

    db.init_db()

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("lisans", make_license))

    conversation = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(menu)
        ],
        states={
            WAIT_LICENSE: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    save_license
                )
            ],
            WAIT_API_ID: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    api_id_step
                )
            ],
            WAIT_API_HASH: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    api_hash_step
                )
            ],
            WAIT_PHONE: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    phone_step
                )
            ],
            WAIT_OTP: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    otp_step
                )
            ],
            WAIT_MSG: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    save_msg
                )
            ],
            WAIT_GROUPS: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    save_groups
                )
            ],
            WAIT_INTERVAL: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    save_interval
                )
            ]
        },
        fallbacks=[
            CommandHandler("start", start)
        ],
        per_user=True,
        per_chat=True
    )

    app.add_handler(conversation)

    print("Bot çalışıyor...")
    app.run_polling()


if __name__ == "__main__":
    main()
telegram_userbot_manager/
│
├── bot.py
├── database.py
├── userbot_manager.py
├── requirements.txt
├── .env.example
├── .gitignore
│
└── sessions/
