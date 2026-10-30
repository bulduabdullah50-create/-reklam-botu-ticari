import os
import asyncio
import logging

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

import database as db

TOKEN = os.environ["BOT_TOKEN"]

logging.basicConfig(level=logging.INFO)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    db.create_user(uid)

    await update.message.reply_text(
        "FARKETMEZDA PANELİ\n\n"
        "/durum - Hesap durumun\n"
        "/reklam METİN - Reklam metnini kaydet\n"
        "/aralik SANİYE - Gönderim aralığı\n"
        "/gruplar - İzinli grupların\n"
        "/izinle GRUP_ID İSİM - İzinli grup ekle\n"
        "/izinkaldir GRUP_ID - Grup iznini kaldır\n"
        "/baslat - Gönderimi etkinleştir\n"
        "/durdur - Gönderimi durdur\n\n"
        "Telegram kişisel hesabı bağlantısı ayrıca güvenli "
        "oturum kurulumu gerektirir."
    )


async def reklam(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = " ".join(context.args).strip()

    if not text:
        await update.message.reply_text("Kullanım: /reklam Reklam metni")
        return

    db.create_user(uid)
    db.update_user(uid, ad_text=text)

    await update.message.reply_text("Reklam metni kaydedildi.")


async def aralik(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id

    try:
        seconds = int(context.args[0])
        if not 300 <= seconds <= 86400:
            raise ValueError
    except (IndexError, ValueError):
        await update.message.reply_text("Kullanım: /aralik 3600")
        return

    db.create_user(uid)
    db.update_user(uid, interval=seconds)

    await update.message.reply_text(
        f"Aralık {seconds} saniye olarak kaydedildi."
    )


async def izinle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id

    try:
        chat_id = int(context.args[0])
        title = " ".join(context.args[1:]).strip()
        if not title:
            raise ValueError
    except (IndexError, ValueError):
        await update.message.reply_text(
            "Kullanım: /izinle GRUP_ID Grup adı"
        )
        return

    db.create_user(uid)
    db.add_group(uid, chat_id, title)

    await update.message.reply_text(
        "Grup kendi izin listene eklendi. "
        "Grup yöneticisinin açık iznini ayrıca doğrula."
    )


async def izinkaldir(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id

    try:
        chat_id = int(context.args[0])
    except (IndexError, ValueError):
        await update.message.reply_text("Kullanım: /izinkaldir GRUP_ID")
        return

    db.remove_group(uid, chat_id)
    await update.message.reply_text("Grup izin listenden çıkarıldı.")


async def gruplar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    groups = db.get_groups(uid)

    if not groups:
        await update.message.reply_text("Henüz kayıtlı grubun yok.")
        return

    result = "\n".join(
        f"{g['title']} — {g['chat_id']}" for g in groups
    )
    await update.message.reply_text(result)


async def baslat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user = db.get_user(uid)

    if not user or not user["ad_text"]:
        await update.message.reply_text("Önce /reklam ile metin kaydet.")
        return

    if not db.get_groups(uid):
        await update.message.reply_text("Önce izinli grup ekle.")
        return

    db.update_user(uid, enabled=1)
    await update.message.reply_text(
        "Gönderim isteğin etkinleştirildi. "
        "Gönderim motoru yalnızca doğrulanmış izinli hedeflerle çalışmalıdır."
    )


async def durdur(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db.create_user(update.effective_user.id)
    db.update_user(update.effective_user.id, enabled=0)
    await update.message.reply_text("Gönderim durduruldu.")


async def durum(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user = db.get_user(uid)

    if not user:
        await update.message.reply_text("Henüz ayarın yok.")
        return

    await update.message.reply_text(
        f"Reklam kayıtlı: {bool(user['ad_text'])}\n"
        f"Aralık: {user['interval']} saniye\n"
        f"İzinli grup: {len(db.get_groups(uid))}\n"
        f"Etkin: {bool(user['enabled'])}"
    )


def main():
    db.init_db()

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reklam", reklam))
    app.add_handler(CommandHandler("aralik", aralik))
    app.add_handler(CommandHandler("izinle", izinle))
    app.add_handler(CommandHandler("izinkaldir", izinkaldir))
    app.add_handler(CommandHandler("gruplar", gruplar))
    app.add_handler(CommandHandler("baslat", baslat))
    app.add_handler(CommandHandler("durdur", durdur))
    app.add_handler(CommandHandler("durum", durum))

    app.run_polling()


if __name__ == "__main__":
    main()
