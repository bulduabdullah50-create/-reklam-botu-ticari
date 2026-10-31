import os
import re
import asyncio
import secrets
import string
import logging
from datetime import datetime
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

blacklisted_groups = {}


def is_admin(uid):
    return int(uid) == ADMIN_ID


def has_valid_access(uid):
    return is_admin(uid) or db.license_valid(uid)


def get_keyboard(uid):
    rows = [
        [
            InlineKeyboardButton("➕ Hesap Ekle", callback_data="add"),
            InlineKeyboardButton("❌ Hesap Çıkar", callback_data="remove_acc"),
        ],
        [
            InlineKeyboardButton("👤 Hesabım", callback_data="account"),
            InlineKeyboardButton("📩 Reklam Seç", callback_data="ad"),
        ],
        [
            InlineKeyboardButton("🚫 Grup Engel", callback_data="blacklist"),
            InlineKeyboardButton("⏱ Grup Aralığı", callback_data="time_group"),
        ],
        [
            InlineKeyboardButton("⏳ Tur Süresi", callback_data="time_loop"),
            InlineKeyboardButton("📊 Durum", callback_data="status"),
        ],
        [
            InlineKeyboardButton("▶ Başlat", callback_data="start_send"),
            InlineKeyboardButton("⏹ Durdur", callback_data="stop_send"),
        ],
        [
            InlineKeyboardButton("🔑 KEY Kullan", callback_data="redeem"),
            InlineKeyboardButton(
                "💬 İletişim",
                url="https://t.me/farketmezda"
            ),
        ],
    ]

    if is_admin(uid):
        rows.append([
            InlineKeyboardButton("👑 Yönetici Paneli", callback_data="admin")
        ])

    return InlineKeyboardMarkup(rows)


def sub_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅ Geri / Ana Menü", callback_data="home")]
    ])


def admin_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ KEY Oluştur", callback_data="key_create")],
        [InlineKeyboardButton("📋 KEY Listesi", callback_data="key_list")],
        [InlineKeyboardButton("⬅ Ana Menü", callback_data="home")],
    ])


async def get_client(uid):
    client = clients.get(uid)

    if client:
        try:
            if not client.is_connected():
                await client.connect()
            if await client.is_user_authorized():
                return client
        except Exception:
            pass

    path = SESSIONS / str(uid)
    session_file = Path(str(path) + ".session")

    if not session_file.exists():
        return None

    try:
        client = TelegramClient(str(path), API_ID, API_HASH)
        await client.connect()

        if not await client.is_user_authorized():
            await client.disconnect()
            return None

        clients[uid] = client
        return client
    except Exception:
        log.exception("Client yükleme hatası")
        return None


def get_license_info_text(uid):
    if is_admin(uid):
        return "👑 Sınırsız (Yönetici)"
    
    user = db.get_user(uid)
    if not user or not db.license_valid(uid):
        return "❌ Aktif Lisans Yok"
    
    expiry = user.get("license_expiry")
    if not expiry:
        return "✅ Aktif"
    
    try:
        if isinstance(expiry, str):
            exp_date = datetime.fromisoformat(expiry)
        else:
            exp_date = expiry
        
        remaining = exp_date - datetime.utcnow()
        days = remaining.days
        hours = remaining.seconds // 3600
        return f"✅ Aktif ({days} Gün {hours} Saat Kalan)"
    except Exception:
        return "✅ Aktif"


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


async def start_sender(uid, message_obj, context):
    if not has_valid_access(uid):
        await message_obj.reply_text("❌ Aktif lisansın bulunmuyor! Lütfen geçerli bir KEY kullan.")
        return

    user = db.get_user(uid)

    client = await get_client(uid)
    if not client:
        await message_obj.reply_text("❌ Reklam yapacak Telegram hesabı bağlı değil! Önce 'Hesap Ekle' ile numarayı bağla.")
        return

    if not user or not user.get("ad_message_id"):
        await message_obj.reply_text("❌ Önce bağladığın hesabın Kayıtlı Mesajlar'ından 'Reklam Seç' bölümünü kullan.")
        return

    current = tasks.get(uid)
    if current and not current.done():
        await message_obj.reply_text("⚠️ Gönderim zaten çalışıyor.")
        return

    db.set_user(uid, enabled=1)
    tasks[uid] = asyncio.create_task(sender_loop(uid, context, message_obj.chat_id))
    await message_obj.reply_text("▶ Otomatik gönderim başlatıldı! Farklı hesap üzerinden gruplara mesajlar akmaya başlayacak.")


async def sender_loop(uid, context, chat_id):
    try:
        while True:
            user = db.get_user(uid)

            if not user or not user.get("enabled", 0):
                break

            if not has_valid_access(uid):
                break

            client = await get_client(uid)
            if not client:
                break

            message_id = user.get("ad_message_id")
            if not message_id:
                break

            try:
                if not client.is_connected():
                    await client.connect()

                # Bağlı olan farklı hesabın Kayıtlı Mesajlar'ından reklamı çekiyoruz
                messages = await client.get_messages("me", ids=message_id)
                if not messages:
                    msgs = await client.get_messages("me", limit=1)
                    source_msg = msgs[0] if msgs else None
                else:
                    source_msg = messages

            except Exception as e:
                log.exception("Reklam mesajı alınamadı: %s", e)
                break

            if not source_msg:
                try:
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text="❌ Bağlı hesabın Kayıtlı Mesajlar'ında reklam bulunamadı! Lütfen o hesaba reklamı atıp tekrar 'Reklam Seç' yap."
                    )
                except Exception:
                    pass
                break

            try:
                dialogs = await client.get_dialogs()
                groups = []
                for d in dialogs:
                    if d.is_group or d.is_supergroup or getattr(d.entity, 'megagroup', False):
                        groups.append(d)
                
                if not groups:
                    groups = [d for d in dialogs if d.is_group or d.is_supergroup]

            except Exception as e:
                log.exception("Gruplar çekilirken hata: %s", e)
                await asyncio.sleep(10)
                continue

            user_bl = blacklisted_groups.get(uid, set())
            
            loop_interval = user.get("loop_interval_seconds", 240)
            if loop_interval is None:
                loop_interval = 240
                
            group_interval = user.get("interval_seconds", 3)
            if group_interval is None:
                group_interval = 3

            success_count = 0
            fail_count = 0
            skipped_count = 0

            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"🚀 **Tur Başladı!**\nBağlı hesabın üye olduğu grup sayısı: {len(groups)}"
                )
            except Exception:
                pass

            for group in groups:
                user = db.get_user(uid)
                if not user or not user.get("enabled", 0):
                    break

                g_id = str(group.id)
                g_username = f"@{group.entity.username}".lower() if getattr(group.entity, 'username', None) else ""
                g_title = getattr(group, 'name', 'İsimsiz Grup')

                if g_id in user_bl or (g_username and g_username in user_bl):
                    skipped_count += 1
                    continue

                try:
                    await client.forward_messages(
                        entity=group.id,
                        messages=source_msg
                    )
                    success_count += 1
                    try:
                        await context.bot.send_message(
                            chat_id=chat_id,
                            text=f"✅ Gönderildi: **{g_title}**"
                        )
                    except Exception:
                        pass

                except FloodWaitError as exc:
                    log.warning("Telegram bekleme sınırı: %s saniye", exc.seconds)
                    try:
                        await context.bot.send_message(
                            chat_id=chat_id,
                            text=f"⏳ Telegram Flood Sınırı! {exc.seconds} saniye bekleniyor..."
                        )
                    except Exception:
                        pass
                    await asyncio.sleep(exc.seconds + 5)
                except Exception as e:
                    fail_count += 1
                    log.exception("Gönderim hatası (%s): %s", g_title, e)

                if group_interval > 0:
                    await asyncio.sleep(group_interval)

            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"📊 **Tur Tamamlandı Raporu**\n\n"
                         f"✅ Başarılı: {success_count}\n"
                         f"❌ Hatalı: {fail_count}\n"
                         f"🚫 Engellenen: {skipped_count}\n\n"
                         f"⏳ Sıradaki tur için {loop_interval} saniye bekleniyor..."
                )
            except Exception:
                pass

            user = db.get_user(uid)
            if not user or not user.get("enabled", 0):
                break

            if loop_interval > 0:
                await asyncio.sleep(loop_interval)

    except asyncio.CancelledError:
        raise
    except Exception as e:
        log.exception("Gönderim döngüsü genel hata: %s", e)
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
        if not has_valid_access(uid):
            await q.edit_message_text(
                "❌ **Erişim Engellendi!**\nHesap eklemek için aktif lisansınızın bulunması gerekir.\nLütfen önce '🔑 KEY Kullan' butonundan kodunuzu girin.",
                reply_markup=sub_menu_keyboard()
            )
            return

        context.user_data["flow"] = "phone"
        await q.edit_message_text(
            "➕ REKLAM ATACAK HESABI EKLE\n\n"
            "Reklam yapacak olan diğer Telegram numaranı ülke koduyla gönder.\n"
            "Örnek: +905xxxxxxxxx\n\n"
            "Doğrulama kodunu ve şifreni kimseyle paylaşma.",
            reply_markup=sub_menu_keyboard()
        )

    elif action == "remove_acc":
        await stop_sender(uid)
        client = clients.pop(uid, None)
        if client:
            try:
                await client.log_out()
            except Exception:
                try:
                    await client.disconnect()
                except Exception:
                    pass

        path = SESSIONS / str(uid)
        session_file = Path(str(path) + ".session")
        if session_file.exists():
            try:
                session_file.unlink()
            except Exception:
                pass

        await q.edit_message_text(
            "✅ **Reklam Hesabı Bağlantısı Kesildi ve Çıkarıldı.**\n\nYeni bir hesap bağlamak için '➕ Hesap Ekle' butonunu kullanabilirsin.",
            reply_markup=get_keyboard(uid)
        )

    elif action == "account":
        path = SESSIONS / str(uid)
        exists = Path(str(path) + ".session").exists()
        lic_status = get_license_info_text(uid)
        await q.edit_message_text(
            f"👤 HESAP BİLGİLERİ\n\n"
            f"Reklam Hesabı Oturumu: {'✅ Bağlı' if exists else '❌ Bağlı değil'}\n"
            f"Lisans Süresi: {lic_status}",
            reply_markup=get_keyboard(uid)
        )

    elif action == "ad":
        client = await get_client(uid)

        if not client:
            await q.edit_message_text(
                "❌ Önce reklam atacak farklı hesabını bağla.",
                reply_markup=get_keyboard(uid)
            )
            return

        try:
            if not client.is_connected():
                await client.connect()

            messages = await client.get_messages("me", limit=1)

            if not messages:
                await q.edit_message_text(
                    "❌ Bağladığın hesabın Kayıtlı Mesajlar'ı boş. Lütfen o hesaptan Kayıtlı Mesajlar'a reklamını at.",
                    reply_markup=get_keyboard(uid)
                )
                return

            db.set_user(uid, ad_message_id=messages[0].id)

            await q.edit_message_text(
                "✅ Bağlı hesabın Son Kayıtlı Mesajı reklam olarak seçildi.",
                reply_markup=get_keyboard(uid)
            )
        except Exception as e:
            log.exception("Reklam seçme hatası: %s", e)
            await q.edit_message_text(
                f"❌ Hata: {str(e)[:100]}",
                reply_markup=get_keyboard(uid)
            )

    elif action == "blacklist":
        context.user_data["flow"] = "blacklist"
        bl_set = blacklisted_groups.get(uid, set())
        bl_text = "\n".join(f"• {x}" for x in bl_set) if bl_set else "Henüz engel yok."
        
        await q.edit_message_text(
            "🚫 GRUP ENGELLEME / KARALİSTE\n\n"
            "Reklam göndermek istemediğin grubun Linkini (t.me/grup) veya Kullanıcı adını (@grup) gönder.\n\n"
            f"📋 Engellenenler:\n{bl_text}",
            reply_markup=sub_menu_keyboard()
        )

    elif action == "time_group":
        user = db.get_user(uid)
        seconds = user["interval_seconds"] if user and user.get("interval_seconds") is not None else 3
        context.user_data["flow"] = "interval_group"

        await q.edit_message_text(
            f"⏱ Mevcut Grup Aralığı: {seconds} saniye\n\n"
            "İki grup arasında beklenecek saniyeyi gönder (Örn: 3):",
            reply_markup=sub_menu_keyboard()
        )

    elif action == "time_loop":
        user = db.get_user(uid)
        loop_sec = user["loop_interval_seconds"] if user and user.get("loop_interval_seconds") is not None else 240
        context.user_data["flow"] = "interval_loop"

        await q.edit_message_text(
            f"⏳ Mevcut Tur Süresi: {loop_sec} saniye ({loop_sec // 60} dakika)\n\n"
            "Tüm gruplar bittikten sonra tur başı beklenecek süreyi saniye olarak gönder (Örn: 240):",
            reply_markup=sub_menu_keyboard()
        )

    elif action == "redeem":
        context.user_data["flow"] = "redeem"
        await q.edit_message_text(
            "🔑 KEY kodunu gönder.",
            reply_markup=sub_menu_keyboard()
        )

    elif action == "start_send":
        await start_sender(uid, q.message, context)

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
        grp_int = user.get("interval_seconds", 3) if user and user.get("interval_seconds") is not None else 3
        loop_int = user.get("loop_interval_seconds", 240) if user and user.get("loop_interval_seconds") is not None else 240
        lic_status = get_license_info_text(uid)
        is_enabled = user.get("enabled", 0) if user else 0

        await q.edit_message_text(
            "📊 SİSTEM DURUMU\n\n"
            f"Reklam Hesabı: {'Bağlı' if connected else 'Bağlı değil'}\n"
            f"Lisans Durumu: {lic_status}\n"
            f"Reklam: {'Seçildi' if user and user.get('ad_message_id') else 'Seçilmedi'}\n"
            f"Grup Aralığı: {grp_int} saniye\n"
            f"Tur Süresi: {loop_int} saniye\n"
            f"Gönderim Durumu: {'Aktif' if is_enabled else 'Kapalı'}",
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
        await q.edit_message_text(
            "KEY süresini gün olarak gönder. Örnek: 30",
            reply_markup=sub_menu_keyboard()
        )

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
                "Reklam atacak hesaba gelen Telegram doğrulama kodunu gönder."
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
            await update.effective_message.reply_text("İşlem sona erdi. /start ile yeniden başla.")
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
                "✅ Reklam atacak hesap başarıyla bağlandı! Şimdi 'Reklam Seç' butonunu kullanabilirsin.",
                reply_markup=get_keyboard(uid)
            )
        except SessionPasswordNeededError:
            context.user_data["flow"] = "2fa"
            await update.effective_message.reply_text("İki adımlı doğrulama (2FA) şifreni gönder.")
        except (PhoneCodeInvalidError, PhoneCodeExpiredError):
            await update.effective_message.reply_text("Kod geçersiz veya süresi dolmuş.")
        except Exception:
            log.exception("Giriş hatası")
            await update.effective_message.reply_text("Hesap bağlantısı tamamlanamadı.")

    elif flow == "2fa":
        item = pending.get(uid)
        if not item:
            context.user_data.clear()
            await update.effective_message.reply_text("İşlem sona erdi. /start ile yeniden başla.")
            return

        try:
            await item["client"].sign_in(password=text)
            clients[uid] = item["client"]
            pending.pop(uid, None)
            context.user_data.clear()

            await update.effective_message.reply_text(
                "✅ Reklam atacak hesap başarıyla bağlandı!",
                reply_markup=get_keyboard(uid)
            )
        except PasswordHashInvalidError:
            await update.effective_message.reply_text("Şifre hatalı. Tekrar dene.")
        except Exception:
            log.exception("2FA hatası")
            await update.effective_message.reply_text("Giriş tamamlanamadı.")

    elif flow == "blacklist":
        clean_target = text.replace("https://t.me/", "").replace("t.me/", "").strip()
        if not clean_target.startswith("@") and not clean_target.lstrip("-").isdigit():
            clean_target = "@" + clean_target
        
        clean_target = clean_target.lower()
        
        if uid not in blacklisted_groups:
            blacklisted_groups[uid] = set()
            
        blacklisted_groups[uid].add(clean_target)
        context.user_data.clear()
        
        await update.effective_message.reply_text(
            f"✅ **{clean_target}** reklam gönderim listesinden çıkarıldı ve engellendi.",
            reply_markup=get_keyboard(uid)
        )

    elif flow == "interval_group":
        try:
            seconds = int(text)
            if seconds < 0:
                raise ValueError

            db.set_user(uid, interval_seconds=seconds)
            context.user_data.clear()

            await update.effective_message.reply_text(
                f"✅ Grup arası aralık kaydedildi: {seconds} saniye.",
                reply_markup=get_keyboard(uid)
            )
        except ValueError:
            await update.effective_message.reply_text("Lütfen 0 veya daha büyük bir sayı gir.")

    elif flow == "interval_loop":
        try:
            seconds = int(text)
            if seconds < 0:
                raise ValueError

            db.set_user(uid, loop_interval_seconds=seconds)
            context.user_data.clear()

            await update.effective_message.reply_text(
                f"✅ Tur dinlenme süresi kaydedildi: {seconds} saniye.",
                reply_markup=get_keyboard(uid)
            )
        except ValueError:
            await update.effective_message.reply_text("Lütfen 0 veya daha büyük bir sayı gir.")

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
                f"✅ KEY OLUŞTURULDU\n\n`{code}`\n\nSüre: {days} gün",
                parse_mode="Markdown",
                reply_markup=admin_keyboard()
            )
        except ValueError:
            await update.effective_message.reply_text("1 ile 3650 arasında gün gir.")


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

    app.add_handler(CommandHandler("start", lambda u, c: start(u, c)))
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler)
    )

    app.run_polling()


if __name__ == "__main__":
    main()
