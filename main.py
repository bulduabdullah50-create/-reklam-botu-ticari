import os
import re
import asyncio
import secrets
import string
import logging
from pathlib import Path

from telethon import TelegramClient
from telethon.errors import (
    SessionPasswordNeededError,
    PhoneCodeInvalidError,
    PhoneCodeExpiredError,
    PasswordHashInvalidError,
    FloodWaitError,
)

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters
)

import database as db

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("farketmezda")

BOT_TOKEN = os.environ["BOT_TOKEN"]
API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
ADMIN_ID = int(os.environ["ADMIN_ID"])

DATA_DIR = Path(os.getenv("DATA_DIR", "data"))
SESSIONS = DATA_DIR / "sessions"
SESSIONS.mkdir(parents=True, exist_ok=True)

clients = {}
pending = {}
tasks = {}


def is_admin(uid):
    return int(uid) == ADMIN_ID


def get_keyboard(uid):
    rows = [
        [
            InlineKeyboardButton("➕ Hesap Ekle", callback_data="add"),
            InlineKeyboardButton("👤 Hesabım", callback_data="account"),
        ],
        [
            InlineKeyboardButton("👥 Gruplarım", callback_data="groups"),
        ],
        [
            InlineKeyboardButton("📩 Reklam Seç", callback_data="ad"),
            InlineKeyboardButton("⏱ Zaman Ayarı", callback_data="time"),
        ],
        [
            InlineKeyboardButton("▶ Başlat", callback_data="start_send"),
            InlineKeyboardButton("⏹ Durdur", callback_data="stop_send"),
        ],
        [
            InlineKeyboardButton("🔑 KEY Kullan", callback_data="redeem"),
            InlineKeyboardButton("📊 Durum", callback_data="status"),
        ],
        [
            InlineKeyboardButton(
                "💬 İletişim",
                url="https://t.me/farketmezda"
            )
        ],
    ]

    if is_admin(uid):
        rows.append([
            InlineKeyboardButton("👑 Yönetici Paneli", callback_data="admin")
        ])

    return InlineKeyboardMarkup(rows)


def admin_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ KEY Oluştur", callback_data="key_create")],
        [InlineKeyboardButton("📋 KEY Listesi", callback_data="key_list")],
        [InlineKeyboardButton("⬅ Ana Menü", callback_data="home")],
    ])


async def get_client(uid):
    client = clients.get(uid)

    if client and client.is_connected():
        return client

    path = SESSIONS / str(uid)

    if not Path(str(path) + ".session").exists():
        return None

    client = TelegramClient(str(path), API_ID, API_HASH)
    await client.connect()

    if not await client.is_user_authorized():
        await client.disconnect()
        return None

    clients[uid] = client
    return client


async def show_home(update, context):
    uid = update.effective_user.id
    db.ensure_user(uid, update.effective_user.username)

    text = (
        "💜 FARKETMEZDA\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "NEON YÖNETİM PANELİ\n\n"
        "Hoş geldin!\n"
        "İşlem yapmak için menüden seçim yap."
    )

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            text, reply_markup=get_keyboard(uid)
        )
    else:
        await update.effective_message.reply_text(
            text, reply_markup=get_keyboard(uid)
        )


async def start(update, context):
    context.user_data.clear()
    await show_home(update, context)


async def start_sender(uid, message):
    user = db.get_user(uid)

    if not user or not db.license_valid(uid):
        await message.reply_text("❌ Aktif lisansın bulunmuyor.")
        return

    client = await get_client(uid)

    if not client:
        await message.reply_text("Önce Telegram hesabını bağla.")
        return

    if not user["ad_message_id"]:
        await message.reply_text("Önce Reklam Seç bölümünü kullan.")
        return

    try:
        dialogs = await client.get_dialogs()
        groups = [d for d in dialogs if d.is_group or d.is_supergroup]
        
        for g in groups:
            db.approve_group(uid, g.id, g.title or str(g.id), uid)
    except Exception:
        log.exception("Gruplar otomatik senkronize edilirken hata oluştu")

    approved_groups = db.groups_for(uid)

    if not approved_groups:
        await message.reply_text("Gönderim yapabileceğin grup bulunamadı.")
        return

    current = tasks.get(uid)

    if current and not current.done():
        await message.reply_text("Gönderim zaten çalışıyor.")
        return

    db.set_user(uid, enabled=1)

    tasks[uid] = asyncio.create_task(sender_loop(uid))

    await message.reply_text("▶ Gönderim başlatıldı. Tüm gruplara mesaj iletilecek.")


async def sender_loop(uid):
    try:
        while True:
            user = db.get_user(uid)

            if not user or not user["enabled"]:
                break

            if not db.license_valid(uid):
                break

            client = await get_client(uid)

            if not client:
                break

            message_id = user["ad_message_id"]

            if not message_id:
                break

            source = await client.get_messages("me", ids=message_id)

            if not source:
                break

            groups = db.groups_for(uid)

            for group in groups:
                user = db.get_user(uid)

                if not user or not user["enabled"]:
                    break

                try:
                    await client.forward_messages(
                        entity=group["chat_id"],
                        messages=source
                    )

                except FloodWaitError as exc:
                    log.warning("Telegram bekleme sınırı: %s saniye", exc.seconds)
                    await asyncio.sleep(exc.seconds + 5)

                except Exception:
                    log.exception(
                        "Gönderim hatası: kullanıcı=%s grup=%s",
                        uid, group["chat_id"]
                    )

                await asyncio.sleep(3)

            user = db.get_user(uid)

            if not user or not user["enabled"]:
                break

            await asyncio.sleep(max(900, int(user["interval_seconds"])))

    except asyncio.CancelledError:
        raise

    except Exception:
        log.exception("Gönderim döngüsü hatası")

    finally:
        db.set_user(uid, enabled=0)
        tasks.pop(uid, None)


async def stop_sender(uid):
    db.set_user(uid, enabled=0)

    task = tasks.get(uid)

    if task and not task.done():
        task.cancel()


async def button(update, context):
    q = update.callback_query
    await q.answer()

    uid = q.from_user.id
    db.ensure_user(uid, q.from_user.username)
    action = q.data

    if action == "home":
        await q.edit_message_text(
            "💜 FARKETMEZDA NEON PANEL",
            reply_markup=get_keyboard(uid)
        )

    elif action == "add":
        context.user_data["flow"] = "phone"
        await q.edit_message_text(
            "➕ HESAP EKLE\n\n"
            "Telefon numaranı ülke koduyla gönder.\n"
            "Örnek: +905xxxxxxxxx\n\n"
            "Doğrulama kodunu ve şifreni kimseyle paylaşma."
        )

    elif action == "account":
        path = SESSIONS / str(uid)
        exists = Path(str(path) + ".session").exists()
        await q.edit_message_text(
            f"👤 Hesap: {'Bağlı' if exists else 'Bağlı değil'}",
            reply_markup=get_keyboard(uid)
        )

    elif action == "groups":
        client = await get_client(uid)

        if not client:
            await q.edit_message_text(
                "Önce hesap ekle.",
                reply_markup=get_keyboard(uid)
            )
            return

        dialogs = await client.get_dialogs()
        groups = [d for d in dialogs if d.is_group or d.is_supergroup]

        for g in groups:
            db.approve_group(uid, g.id, g.title or str(g.id), uid)

        text = "👥 TÜM GRUPLARIN (Otomatik Eklendi)\n\n"

        text += "\n".join(
            f"• {g.name} — {g.id}" for g in groups[:40]
        ) if groups else "Grup bulunamadı."

        await q.edit_message_text(
            text[:3900],
            reply_markup=get_keyboard(uid)
        )

    elif action == "ad":
        client = await get_client(uid)

        if not client:
            await q.edit_message_text(
                "Önce hesabını bağla.",
                reply_markup=get_keyboard(uid)
            )
            return

        messages = await client.get_messages("me", limit=1)

        if not messages:
            await q.edit_message_text(
                "Kayıtlı Mesajlar boş.",
                reply_markup=get_keyboard(uid)
            )
            return

        db.set_user(uid, ad_message_id=messages[0].id)

        await q.edit_message_text(
            "✅ Son Kayıtlı Mesaj reklam olarak seçildi.",
            reply_markup=get_keyboard(uid)
        )

    elif action == "time":
        user = db.get_user(uid)
        seconds = user["interval_seconds"] if user else 3600

        context.user_data["flow"] = "interval"

        await q.edit_message_text(
            f"⏱ Mevcut süre: {seconds} saniye\n\n"
            "Yeni süreyi saniye olarak gönder.\n"
            "Örnek: 3600\n"
            "Minimum: 900 saniye"
        )

    elif action == "redeem":
        context.user_data["flow"] = "redeem"
        await q.edit_message_text("🔑 KEY kodunu gönder.")

    elif action == "start_send":
        await start_sender(uid, q.message)

    elif action == "stop_send":
        await stop_sender(uid)
        await q.edit_message_text(
            "⏹ Gönderim durduruldu.",
            reply_markup=get_keyboard(uid)
        )

    elif action == "status":
        user = db.get_user(uid)
        path = SESSIONS / str(uid)
        connected = Path(str(path) + ".session").exists()

        await q.edit_message_text(
            "📊 DURUM\n\n"
            f"Hesap: {'Bağlı' if connected else 'Bağlı değil'}\n"
            f"Hedef grup: {len(db.groups_for(uid))}\n"
            f"Reklam: {'Seçildi' if user['ad_message_id'] else 'Seçilmedi'}\n"
            f"Aralık: {user['interval_seconds']} saniye\n"
            f"Gönderim: {'Aktif' if user['enabled'] else 'Kapalı'}\n"
            f"Lisans: {'Aktif' if db.license_valid(uid) else 'Aktif değil'}",
            reply_markup=get_keyboard(uid)
        )

    elif action == "admin":
        if not is_admin(uid):
            await q.edit_message_text("⛔ Yetkin yok.")
            return

        await q.edit_message_text(
            "👑 YÖNETİCİ PANELİ",
            reply_markup=admin_keyboard()
        )

    elif action == "key_create":
        if not is_admin(uid):
            await q.edit_message_text("⛔ Yetkin yok.")
            return

        context.user_data["flow"] = "key_days"
        await q.edit_message_text("KEY süresini gün olarak gönder. Örnek: 30")

    elif action == "key_list":
        if not is_admin(uid):
            await q.edit_message_text("⛔ Yetkin yok.")
            return

        rows = db.list_licenses()
        text = "📋 KEY LİSTESİ\n\n"

        text += "\n".join(
            f"{r['code']} | {r['duration_days']} gün | "
            f"{'Kullanıldı' if r['is_used'] else 'Boş'}"
            for r in rows
        ) if rows else "Henüz KEY yok."

        await q.edit_message_text(
            text[:3900],
            reply_markup=admin_keyboard()
        )


async def text_handler(update, context):
    uid = update.effective_user.id
    text = update.effective_message.text.strip()
    flow = context.user_data.get("flow")

    if not flow:
        return

    if flow == "phone":
        if not re.fullmatch(r"\+[1-9]\d{7,14}", text):
            await update.effective_message.reply_text(
                "Telefon numaranı +ülke koduyla gönder."
            )
            return

        client = TelegramClient(
            str(SESSIONS / str(uid)), API_ID, API_HASH
        )
        await client.connect()

        try:
            sent = await client.send_code_request(text)

            pending[uid] = {
                "client": client,
                "phone": text,
                "hash": sent.phone_code_hash
            }

            context.user_data["flow"] = "code"

            await update.effective_message.reply_text(
                "Telegram doğrulama kodunu gönder."
            )

        except Exception:
            await client.disconnect()
            log.exception("Kod gönderme hatası")
            await update.effective_message.reply_text(
                "Kod gönderilemedi. Ayarları kontrol et."
            )

    elif flow == "code":
        item = pending.get(uid)

        if not item:
            context.user_data.clear()
            await update.effective_message.reply_text(
                "İşlem sona erdi. /start ile yeniden başla."
            )
            return

        try:
            await item["client"].sign_in(
                phone=item["phone"],
                code=text,
                phone_code_hash=item["hash"]
            )

            clients[uid] = item["client"]
            pending.pop(uid, None)
            context.user_data.clear()

            await update.effective_message.reply_text(
                "✅ Hesap bağlandı.",
                reply_markup=get_keyboard(uid)
            )

        except SessionPasswordNeededError:
            context.user_data["flow"] = "2fa"
            await update.effective_message.reply_text(
                "İki adımlı doğrulama şifreni gönder."
            )

        except (PhoneCodeInvalidError, PhoneCodeExpiredError):
            await update.effective_message.reply_text(
                "Kod geçersiz veya süresi dolmuş."
            )

        except Exception:
            log.exception("Giriş hatası")
            await update.effective_message.reply_text(
                "Hesap bağlantısı tamamlanamadı."
            )

    elif flow == "2fa":
        item = pending.get(uid)

        if not item:
            context.user_data.clear()
            await update.effective_message.reply_text(
                "İşlem sona erdi. /start ile yeniden başla."
            )
            return

        try:
            await item["client"].sign_in(password=text)

            clients[uid] = item["client"]
            pending.pop(uid, None)
            context.user_data.clear()

            await update.effective_message.reply_text(
                "✅ Hesap bağlandı.",
                reply_markup=get_keyboard(uid)
            )

        except PasswordHashInvalidError:
            await update.effective_message.reply_text(
                "Şifre hatalı. Tekrar dene."
            )

        except Exception:
            log.exception("2FA hatası")
            await update.effective_message.reply_text(
                "Giriş tamamlanamadı."
            )

    elif flow == "interval":
        try:
            seconds = int(text)

            if not 900 <= seconds <= 86400:
                raise ValueError

            db.set_user(uid, interval_seconds=seconds)
            context.user_data.clear()

            await update.effective_message.reply_text(
                f"✅ Aralık kaydedildi: {seconds} saniye.",
                reply_markup=get_keyboard(uid)
            )

        except ValueError:
            await update.effective_message.reply_text(
                "900 ile 86400 arasında saniye gir."
            )

    elif flow == "redeem":
        ok, message = db.use_license(uid, text)
        context.user_data.clear()

        await update.effective_message.reply_text(
            ("✅ " if ok else "❌ ") + message,
            reply_markup=get_keyboard(uid)
        )

    elif flow == "key_days":
        if not is_admin(uid):
            context.user_data.clear()
            await update.effective_message.reply_text("Yetkin yok.")
            return

        try:
            days = int(text)

            if not 1 <= days <= 3650:
                raise ValueError

            alphabet = string.ascii_uppercase + string.digits

            code = "FKD-" + "-".join(
                "".join(secrets.choice(alphabet) for _ in range(4))
                for _ in range(3)
            )

            db.create_license(code, days)
            context.user_data.clear()

            await update.effective_message.reply_text(
                f"✅ KEY OLUŞTURULDU\n\n{code}\nSüre: {days} gün",
                reply_markup=admin_keyboard()
            )

        except ValueError:
            await update.effective_message.reply_text(
                "1 ile 3650 arasında gün gir."
            )


async def aralik(update, context):
    uid = update.effective_user.id

    if not context.args:
        user = db.get_user(uid)
        await update.effective_message.reply_text(
            f"Mevcut aralık: {user['interval_seconds']} saniye\n"
            "Kullanım: /aralik 3600"
        )
        return

    try:
        seconds = int(context.args[0])

        if not 900 <= seconds <= 86400:
            raise ValueError

        db.set_user(uid, interval_seconds=seconds)

        await update.effective_message.reply_text(
            f"Aralık kaydedildi: {seconds} saniye."
        )

    except ValueError:
        await update.effective_message.reply_text(
            "900–86400 saniye arasında değer gir."
        )


async def zaman(update, context):
    user = db.get_user(update.effective_user.id)

    await update.effective_message.reply_text(
        f"⏱ Mevcut aralığın: {user['interval_seconds']} saniye."
    )


async def admin_command(update, context):
    uid = update.effective_user.id

    if not is_admin(uid):
        await update.effective_message.reply_text("⛔ Yetkin yok.")
        return

    await update.effective_message.reply_text(
        "👑 YÖNETİCİ PANELİ",
        reply_markup=admin_keyboard()
    )


async def shutdown(app):
    for item in list(pending.values()):
        try:
            await item["client"].disconnect()
        except Exception:
            pass

    for client in list(clients.values()):
        try:
            await client.disconnect()
        except Exception:
            pass


def main():
    db.init_db()

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_shutdown(shutdown)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("aralik", aralik))
    app.add_handler(CommandHandler("zaman", zaman))
    app.add_handler(CommandHandler("admin", admin_command))

    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler)
    )

    app.run_polling()


if __name__ == "__main__":
    main()
