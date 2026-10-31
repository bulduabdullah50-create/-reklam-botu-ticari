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
tur_detaylari = {}  # Hangi hesap hangi grupta ne yapmış tutmak için


def is_admin(uid):
    return int(uid) == ADMIN_ID


def has_valid_access(uid):
    return is_admin(uid) or db.license_valid(uid)


def get_keyboard(uid):
    rows = [
        [
            InlineKeyboardButton("➕ Hesap Ekle", callback_data="add"),
            InlineKeyboardButton("👤 Hesaplarım", callback_data="accounts"),
        ],
        [
            InlineKeyboardButton("📩 Reklam Seç", callback_data="ad"),
            InlineKeyboardButton("🚫 Grup Engel", callback_data="blacklist"),
        ],
        [
            InlineKeyboardButton("⏱ Grup Aralığı", callback_data="time_group"),
            InlineKeyboardButton("⏳ Tur Süresi", callback_data="time_loop"),
        ],
        [
            InlineKeyboardButton("📊 Durum", callback_data="status"),
            InlineKeyboardButton("🔑 KEY Kullan", callback_data="redeem"),
        ],
        [
            InlineKeyboardButton("💬 İletişim", url="https://t.me/farketmezda"),
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


async def get_client(session_name):
    client = clients.get(session_name)
    if client:
        try:
            if not client.is_connected():
                await client.connect()
            if await client.is_user_authorized():
                return client
        except Exception:
            pass

    path = SESSIONS / session_name
    session_file = Path(str(path) + ".session")

    if not session_file.exists():
        return None

    try:
        client = TelegramClient(str(path), API_ID, API_HASH)
        await client.connect()

        if not await client.is_user_authorized():
            await client.disconnect()
            return None

        clients[session_name] = client
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


async def start_sender(uid, session_name, message_obj, context):
    if not has_valid_access(uid):
        await message_obj.reply_text("❌ Aktif lisansın bulunmuyor! Lütfen geçerli bir KEY kullan.")
        return

    user = db.get_user(uid)

    client = await get_client(session_name)
    if not client:
        await message_obj.reply_text(f"❌ '{session_name}' oturumu bulunamadı veya süresi dolmuş.")
        return

    if not user or not user.get("ad_message_id"):
        await message_obj.reply_text("❌ Önce bağladığın hesabın Kayıtlı Mesajlar'ından 'Reklam Seç' bölümünü kullan.")
        return

    task_key = f"{uid}_{session_name}"
    current = tasks.get(task_key)
    if current and not current.done():
        await message_obj.reply_text(f"⚠️ '{session_name}' için gönderim zaten çalışıyor.")
        return

    tasks[task_key] = asyncio.create_task(sender_loop(uid, session_name, context, message_obj.chat_id))
    await message_obj.reply_text(f"▶ **{session_name}** hesabı ile otomatik gönderim başlatıldı!")


async def sender_loop(uid, session_name, context, chat_id):
    try:
        while True:
            if not has_valid_access(uid):
                break

            client = await get_client(session_name)
            if not client:
                break

            user = db.get_user(uid)
            message_id = user.get("ad_message_id") if user else None
            if not message_id:
                break

            try:
                if not client.is_connected():
                    await client.connect()

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
                        text=f"❌ '{session_name}' hesabının Kayıtlı Mesajlar'ında reklam bulunamadı!"
                    )
                except Exception:
                    pass
                break

            ad_text = getattr(source_msg, 'text', '') or getattr(source_msg, 'message', '')
            if not ad_text:
                ad_text = "Reklam İçeriği"

            try:
                dialogs = await client.get_dialogs()
                groups = []
                for d in dialogs:
                    entity = getattr(d, 'entity', None)
                    if entity:
                        is_chan = getattr(entity, 'broadcast', False)
                        is_grp = getattr(entity, 'megagroup', False) or d.is_group
                        
                        # Kullanıcı ayarına göre kanal veya grupları dahil etme seçeneği
                        include_groups = user.get("inc_groups", 1)
                        include_channels = user.get("inc_channels", 1)

                        if is_grp and not include_groups:
                            continue
                        if is_chan and not include_channels:
                            continue

                        if is_chan or is_grp or getattr(entity, 'title', None):
                            groups.append(d)
                
                if not groups:
                    for d in dialogs:
                        if d.is_group or d.is_channel:
                            groups.append(d)
            except Exception as e:
                log.exception("Gruplar çekilirken hata: %s", e)
                await asyncio.sleep(10)
                continue

            user_bl = blacklisted_groups.get(uid, set())
            
            loop_interval = user.get("loop_interval_seconds", 240) if user else 240
            group_interval = user.get("interval_seconds", 3) if user else 3

            success_count = 0
            fail_count = 0
            skipped_count = 0
            detay_listesi = []

            # Tur Başlangıç Bildirimi (Sohbeti şişirmez, tek mesaj)
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"🚀 **[{session_name}] Tur Başladı!**\nTaranan toplam yer: {len(groups)}"
                )
            except Exception:
                pass

            for group in groups:
                task_key = f"{uid}_{session_name}"
                if task_key not in tasks:
                    break

                g_id = str(group.id)
                entity = getattr(group, 'entity', None)
                g_username = f"@{entity.username}".lower() if entity and getattr(entity, 'username', None) else ""
                g_title = getattr(group, 'name', 'İsimsiz Grup')

                if g_id in user_bl or (g_username and g_username in user_bl):
                    skipped_count += 1
                    detay_listesi.append(f"🚫 Engellendi/Atlandı: {g_title}")
                    continue

                try:
                    await client.send_message(group.id, ad_text)
                    success_count += 1
                    detay_listesi.append(f"✅ Başarılı: {g_title}")
                except FloodWaitError as exc:
                    log.warning("Telegram bekleme sınırı: %s saniye", exc.seconds)
                    detay_listesi.append(f"⏳ Flood Sınırı ({exc.seconds}sn): {g_title}")
                    await asyncio.sleep(exc.seconds + 5)
                except Exception as e:
                    fail_count += 1
                    detay_listesi.append(f"❌ Hatalı: {g_title}")
                    log.exception("Gönderim hatası (%s): %s", g_title, e)

                if group_interval > 0:
                    await asyncio.sleep(group_interval)

            # Detayları hafızaya kaydediyoruz (Butonla istenince bakılacak)
            tur_detaylari[session_name] = detay_listesi

            # Tur Bitti Raporu ve Detay Butonu
            try:
                report_kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("📋 Detaylı Raporu Gör", callback_data=f"report_{session_name}")]
                ])
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"📊 **[{session_name}] Tur Tamamlandı!**\n\n"
                         f"✅ Başarılı: {success_count}\n"
                         f"❌ Hatalı: {fail_count}\n"
                         f"🚫 Engellenen: {skipped_count}\n\n"
                         f"⏳ Sıradaki tur için {loop_interval} saniye bekleniyor...",
                    reply_markup=report_kb
                )
            except Exception:
                pass

            if loop_interval > 0:
                await asyncio.sleep(loop_interval)

    except asyncio.CancelledError:
        raise
    except Exception as e:
        log.exception("Gönderim döngüsü genel hata: %s", e)
    finally:
        task_key = f"{uid}_{session_name}"
        tasks.pop(task_key, None)


async def stop_sender(uid, session_name, chat_id, context):
    task_key = f"{uid}_{session_name}"
    task = tasks.get(task_key)
    if task and not task.done():
        task.cancel()
        tasks.pop(task_key, None)
        await context.bot.send_message(chat_id=chat_id, text=f"⏹ **{session_name}** için gönderim durduruldu.")
    else:
        await context.bot.send_message(chat_id=chat_id, text=f"⚠️ **{session_name}** zaten çalışmıyor.")


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
            "➕ YENİ HESAP EKLE\n\n"
            "Eklenecek Telegram numaranı ülke koduyla gönder.\n"
            "Örnek: +905xxxxxxxxx",
            reply_markup=sub_menu_keyboard()
        )

    elif action == "accounts":
        user_sessions = list(SESSIONS.glob(f"{uid}_*.session"))
        
        if not user_sessions:
            await q.edit_message_text(
                "👤 HESAPLARIM\n\nHenüz eklenmiş bir hesap bulunmuyor.",
                reply_markup=get_keyboard(uid)
            )
            return

        keyboard_rows = []
        for s_file in user_sessions:
            full_name = s_file.stem
            parts = full_name.split("_", 1)
            phone_num = parts[1] if len(parts) > 1 else full_name

            keyboard_rows.append([
                InlineKeyboardButton(f"📱 {phone_num}", callback_data=f"manage_{full_name}")
            ])
        
        keyboard_rows.append([InlineKeyboardButton("⬅ Ana Menü", callback_data="home")])

        await q.edit_message_text(
            "👤 HESAPLARIM\n\nYönetmek veya silmek istediğin hesaba tıkla:",
            reply_markup=InlineKeyboardMarkup(keyboard_rows)
        )

    elif action.startswith("manage_"):
        session_name = action.replace("manage_", "")
        parts = session_name.split("_", 1)
        phone_num = parts[1] if len(parts) > 1 else session_name

        task_key = f"{uid}_{session_name}"
        is_running = task_key in tasks and not tasks[task_key].done()
        status_text = "🟢 Çalışıyor" if is_running else "🔴 Duruyor"

        user = db.get_user(uid) or {}
        inc_g = user.get("inc_groups", 1)
        inc_c = user.get("inc_channels", 1)

        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("▶ Başlat", callback_data=f"start_{session_name}"),
                InlineKeyboardButton("⏹ Durdur", callback_data=f"stop_{session_name}")
            ],
            [
                InlineKeyboardButton(f"👥 Gruplar: {'✅' if inc_g else '❌'}", callback_data=f"toggle_g_{session_name}"),
                InlineKeyboardButton(f"📢 Kanallar: {'✅' if inc_c else '❌'}", callback_data=f"toggle_c_{session_name}")
            ],
            [
                InlineKeyboardButton("❌ Bu Hesabı Sil", callback_data=f"del_{session_name}")
            ],
            [
                InlineKeyboardButton("⬅ Hesaplara Dön", callback_data="accounts")
            ]
        ])

        await q.edit_message_text(
            f"📱 **Hesap Yönetimi:** `{phone_num}`\n\n"
            f"Durum: {status_text}\n"
            f"*(Aşağıdan grup veya kanallara mesaj gitmesini açıp kapatabilirsin)*",
            parse_mode="Markdown",
            reply_markup=kb
        )

    elif action.startswith("toggle_g_"):
        user = db.get_user(uid) or {}
        current = user.get("inc_groups", 1)
        db.set_user(uid, inc_groups=0 if current else 1)
        session_name = action.replace("toggle_g_", "")
        await button(update, context) # Menüyü yenile

    elif action.startswith("toggle_c_"):
        user = db.get_user(uid) or {}
        current = user.get("inc_channels", 1)
        db.set_user(uid, inc_channels=0 if current else 1)
        session_name = action.replace("toggle_c_", "")
        await button(update, context) # Menüyü yenile

    elif action.startswith("report_"):
        session_name = action.replace("report_", "")
        detaylar = tur_detaylari.get(session_name, ["Henüz detay bulunmuyor."])
        metin = f"📋 **{session_name} Son Tur Detayları**\n\n" + "\n".join(detaylar)
        if len(metin) > 4000:
            metin = metin[:3950] + "\n... (Liste çok uzun)"
        
        await q.message.reply_text(metin)

    elif action.startswith("start_"):
        session_name = action.replace("start_", "")
        await start_sender(uid, session_name, q.message, context)

    elif action.startswith("stop_"):
        session_name = action.replace("stop_", "")
        await stop_sender(uid, session_name, q.message.chat_id, context)

    elif action.startswith("del_"):
        session_name = action.replace("del_", "")
        task_key = f"{uid}_{session_name}"
        if task_key in tasks:
            tasks[task_key].cancel()
            tasks.pop(task_key, None)

        client = clients.pop(session_name, None)
        if client:
            try:
                await client.log_out()
            except Exception:
                try:
                    await client.disconnect()
                except Exception:
                    pass

        session_file = SESSIONS / f"{session_name}.session"
        if session_file.exists():
            try:
                session_file.unlink()
            except Exception:
                pass

        await q.edit_message_text(
            f"✅ **{session_name}** hesabı sistemden tamamen çıkarıldı.",
            reply_markup=get_keyboard(uid)
        )

    elif action == "ad":
        user_sessions = list(SESSIONS.glob(f"{uid}_*.session"))
        if not user_sessions:
            await q.edit_message_text("❌ Önce 'Hesaplarım' menüsünden bir hesap ekle.", reply_markup=get_keyboard(uid))
            return

        if len(user_sessions) == 1:
            session_name = user_sessions[0].stem
            client = await get_client(session_name)
            if not client:
                await q.edit_message_text("❌ Hesap oturumu aktif değil.", reply_markup=get_keyboard(uid))
                return
            try:
                if not client.is_connected():
                    await client.connect()
                messages = await client.get_messages("me", limit=1)
                if not messages:
                    await q.edit_message_text("❌ Bu hesabın Kayıtlı Mesajlar'ı boş.", reply_markup=get_keyboard(uid))
                    return
                db.set_user(uid, ad_message_id=messages[0].id)
                await q.edit_message_text(f"✅ [{session_name}] Son Kayıtlı Mesaj reklam olarak seçildi.", reply_markup=get_keyboard(uid))
            except Exception as e:
                await q.edit_message_text(f"❌ Hata: {str(e)[:80]}", reply_markup=get_keyboard(uid))
        else:
            kb_rows = []
            for s_file in user_sessions:
                s_name = s_file.stem
                kb_rows.append([InlineKeyboardButton(f"📩 {s_name} İçin Seç", callback_data=f"pickad_{s_name}")])
            kb_rows.append([InlineKeyboardButton("⬅ Ana Menü", callback_data="home")])
            await q.edit_message_text("Hangi hesabın Kayıtlı Mesajlar'ından reklam seçmek istiyorsun?", reply_markup=InlineKeyboardMarkup(kb_rows))

    elif action.startswith("pickad_"):
        session_name = action.replace("pickad_", "")
        client = await get_client(session_name)
        if not client:
            await q.edit_message_text("❌ Hesap oturumu aktif değil.", reply_markup=get_keyboard(uid))
            return
        try:
            if not client.is_connected():
                await client.connect()
            messages = await client.get_messages("me", limit=1)
            if not messages:
                await q.edit_message_text("❌ Bu hesabın Kayıtlı Mesajlar'ı boş.", reply_markup=get_keyboard(uid))
                return
            db.set_user(uid, ad_message_id=messages[0].id)
            await q.edit_message_text(f"✅ [{session_name}] Son Kayıtlı Mesaj reklam olarak seçildi.", reply_markup=get_keyboard(uid))
        except Exception as e:
            await q.edit_message_text(f"❌ Hata: {str(e)[:80]}", reply_markup=get_keyboard(uid))

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

    elif action == "status":
        user = db.get_user(uid)
        user_sessions = list(SESSIONS.glob(f"{uid}_*.session"))
        grp_int = user.get("interval_seconds", 3) if user and user.get("interval_seconds") is not None else 3
        loop_int = user.get("loop_interval_seconds", 240) if user and user.get("loop_interval_seconds") is not None else 240
        lic_status = get_license_info_text(uid)

        await q.edit_message_text(
            "📊 SİSTEM DURUMU\n\n"
            f"Kayıtlı Hesap Sayısı: {len(user_sessions)}\n"
            f"Lisans Durumu: {lic_status}\n"
            f"Reklam: {'Seçildi' if user and user.get('ad_message_id') else 'Seçilmedi'}\n"
            f"Grup Aralığı: {grp_int} saniye\n"
            f"Tur Süresi: {loop_int} saniye",
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

        session_name = f"{uid}_{text}"
        client = TelegramClient(
            str(SESSIONS / session_name), API_ID, API_HASH
        )
        await client.connect()

        try:
            sent = await client.send_code_request(text)

            pending[uid] = {
                "client": client,
                "phone": text,
                "hash": sent.phone_code_hash,
                "session_name": session_name
            }

            context.user_data["flow"] = "code"

            await update.effective_message.reply_text(
                "Bu numaraya gelen Telegram doğrulama kodunu gönder:"
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
            s_name = item["session_name"]
            clients[s_name] = item["client"]
            pending.pop(uid, None)
            context.user_data.clear()

            await update.effective_message.reply_text(
                f"✅ **{item['phone']}** hesabı başarıyla eklendi!\n'Hesaplarım' menüsünden yönetebilirsin.",
                reply_markup=get_keyboard(uid)
            )
        except SessionPasswordNeededError:
            context.user_data["flow"] = "2fa"
            await update.effective_message.reply_text("İki adımlı doğrulama (2FA) şifreni gönder:")
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
            s_name = item["session_name"]
            clients[s_name] = item["client"]
            pending.pop(uid, None)
            context.user_data.clear()

            await update.effective_message.reply_text(
                "✅ Hesap başarıyla bağlandı!",
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
            f"✅ **{clean_target}** engellenenler listesine eklendi.",
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
