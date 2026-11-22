import os
import re
import asyncio
import logging
import secrets
import string
import datetime
import urllib.request
from threading import Thread
from flask import Flask
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import (
    SessionPasswordNeededError,
    FloodWaitError,
    AuthKeyUnregisteredError,
    UserDeactivatedError,
    UserDeactivatedBanError,
)
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters
import database as db

app_web = Flask(__name__)

@app_web.route('/')
def home_web():
    return "💎 FARKETMEZDA PLATINUM REKLAM BOTU AKTİF!"

@app_web.route('/health')
def health_web():
    return "OK", 200

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app_web.run(host="0.0.0.0", port=port, use_reloader=False)

def _keep_alive_worker():
    port = int(os.environ.get("PORT", 10000))
    render_url = os.environ.get("RENDER_EXTERNAL_URL", "").strip().rstrip("/")
    target_url = f"{render_url}/health" if render_url else f"http://127.0.0.1:{port}/health"
    while True:
        try:
            import time
            time.sleep(240)
            req = urllib.request.Request(target_url, method="GET")
            with urllib.request.urlopen(req, timeout=5) as _:
                pass
        except Exception:
            pass

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("farketmezda")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)

BOT_TOKEN = os.environ["BOT_TOKEN"]
API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
ADMIN_ID = int(os.environ["ADMIN_ID"])

clients, pending, tasks, blacklisted_groups, failed_reports_cache = {}, {}, {}, {}, {}

def is_admin(uid):
    return int(uid) == ADMIN_ID

def has_valid_access(uid):
    return is_admin(uid) or db.license_valid(uid, ADMIN_ID)

def clean_bold_stars(raw_text: str) -> str:
    if not raw_text:
        return ""
    cleaned = re.sub(r"\*\*(.*?)\*\*", r"\1", raw_text, flags=re.DOTALL)
    cleaned = re.sub(r"(?<!\*)\*(?!\*)(.*?)(?<!\*)\*(?!\*)", r"\1", cleaned, flags=re.DOTALL)
    return cleaned

def get_keyboard(uid):
    rows = [
        [InlineKeyboardButton("💎 ABONELİK & FİYATLAR", callback_data="pricing_panel")],
        [InlineKeyboardButton("➕ Hesap Ekle", callback_data="add"), InlineKeyboardButton("👤 Hesaplarım", callback_data="accounts")],
        [InlineKeyboardButton("▶️ Başlat / ⏹ Durdur", callback_data="start_stop_menu")],
        [InlineKeyboardButton("📩 Hesaba Özel Metin & Süreler", callback_data="account_config_menu")],
        [InlineKeyboardButton("📊 Başarısız Raporlar", callback_data="failed_reports")],
        [InlineKeyboardButton("📖 Kurulum & Ödeme Rehberi", callback_data="guide_menu")],
        [InlineKeyboardButton("🚫 Genel Grup Engel", callback_data="blacklist"), InlineKeyboardButton("🔑 KEY Aktif Et", callback_data="redeem")],
        [InlineKeyboardButton("💬 Canlı Destek", url="https://t.me/farketmezda")]
    ]
    if is_admin(uid):
        rows.append([InlineKeyboardButton("👑 YÖNETİCİ LİSANS İPTAL & İSİMLİ KEY ÜRET", callback_data="admin")])
    return InlineKeyboardMarkup(rows)

def sub_menu_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅ Geri / Ana Menü", callback_data="home")]])

def admin_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ İsimli KEY Üret", callback_data="key_create"), InlineKeyboardButton("📋 KEY Listesi & Sahipleri", callback_data="key_list")],
        [InlineKeyboardButton("📢 Duyuru Geç", callback_data="admin_broadcast")],
        [InlineKeyboardButton("⬅ Ana Menü", callback_data="home")]
    ])

def _format_expiry_text(uid: int) -> str:
    if is_admin(uid):
        return "👑 Sınırsız Yönetici Yetkisi"
    try:
        expiry = db.get_user_expiry(uid)
        if not expiry:
            return "Süre Bulunmuyor ❌"
        fmt = "%Y-%m-%d %H:%M:%S.%f" if "." in str(expiry) else "%Y-%m-%d %H:%M:%S"
        exp_dt = datetime.datetime.strptime(str(expiry), fmt)
        remaining = exp_dt - datetime.datetime.now()
        if remaining.total_seconds() > 0:
            days = remaining.days
            hours = remaining.seconds // 3600
            return f"🟢 Aktif ({days} Gün, {hours} Saat Kaldı)"
        return "🔴 Süresi Dolmuş"
    except Exception:
        return "🟢 Aktif"

async def show_home(update, context):
    uid = update.effective_user.id
    username = update.effective_user.username

    asyncio.create_task(asyncio.to_thread(db.ensure_user, uid, username))
    expiry_str = await asyncio.to_thread(_format_expiry_text, uid)

    text = (
        "⚡ **FARKETMEZDA PLATINUM OTO REKLAM** ⚡\n"
        "_Sınırsız Güç, Kesintisiz Tanıtım Altyapısı_\n\n"
        f"👤 **Kullanıcı ID:** `{uid}`\n"
        f"🛡 **Lisans Durumu:** {expiry_str}\n\n"
        "> 👑 **\"Hedef kitlene doğrudan ulaş, reklamlarını otomatik olarak zirveye taşı.\"**\n\n"
        "Aşağıdaki yönetim panelinden hesaplarınızı bağlayabilir, reklam akışını başlatabilir "
        "veya durdurabilirsiniz. Tüm işlemleriniz güvenli altyapımızla koruma altındadır. 👇"
    )
    if update.callback_query:
        try:
            await update.callback_query.answer()
        except Exception:
            pass
        await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(uid))
    else:
        await update.effective_message.reply_text(text, parse_mode="Markdown", reply_markup=get_keyboard(uid))

async def start(update, context):
    context.user_data.clear()
    await show_home(update, context)

async def show_guide(update, context):
    q = update.callback_query
    guide_text = (
        "📖 **FARKETMEZDA PLATINUM | KURULUM VE ÖDEME REHBERİ**\n\n"
        "> **1️⃣ Nasıl Satın Alınır ve Ödeme Nasıl Yapılır?**\n"
        "• Botumuzu kullanmaya başlamak için bütçene uygun paketi seçmelisin *(Günlük 200 TL, Haftalık 400 TL, Aylık 1000 TL)*.\n"
        "• Ödeme ve lisans keyi temini için admin hesabımızla iletişime geç: **`@farketmezda`** 💬\n"
        "• Ödemeniz onaylandığı anda size özel **İsimli Lisans Keyi** anında tanımlanır.\n\n"
        "> **2️⃣ Bot Nasıl Kurulur ve Aktif Edilir?**\n"
        "• **Adım 1:** Lisans keyinizi ilgili alana girerek aktive edin.\n"
        "• **Adım 2:** \"Hesaplarım\" menüsünden Telegram hesaplarınızı bağlayın.\n"
        "• **Adım 3:** Her hesaba özel **reklam metninizi** ister Kayıtlı Mesajlar'dan ister panelden ayarlayın.\n"
        "• **Adım 4:** Grup arası bekleme sürelerini ve tur dinlenme aralıklarını yapılandırın.\n"
        "• **Adım 5:** Ana paneldeki **Başlat/Durdur** butonlarıyla reklam akışını yönetin!"
    )
    await q.edit_message_text(guide_text, parse_mode="Markdown", reply_markup=sub_menu_keyboard())

async def start_sender(uid, chat_id, context, target_s_name=None, force_restart=False):
    valid = await asyncio.to_thread(has_valid_access, uid)
    if not valid:
        return

    task_key = f"{uid}_auto_all_accounts" if not target_s_name else f"{uid}_acc_{target_s_name}"
    existing = tasks.get(task_key)
    if existing and not existing.done():
        if not force_restart:
            return
        tasks.pop(task_key, None)
        existing.cancel()
        try:
            await existing
        except Exception:
            pass

    if not target_s_name:
        await asyncio.to_thread(db.set_user, uid, is_running=1)
    tasks[task_key] = asyncio.create_task(sender_loop(uid, context, chat_id, target_s_name))

async def _build_telethon_client(s_string):
    clean_string = s_string.strip().strip('"').strip("'").replace("\n", "").strip()
    return TelegramClient(
        StringSession(clean_string),
        API_ID,
        API_HASH,
        connection_retries=4,
        retry_delay=2,
        auto_reconnect=False,
        timeout=25
    )

async def _safe_disconnect(client):
    if client is None:
        return
    try:
        if client.is_connected():
            await asyncio.wait_for(client.disconnect(), timeout=5)
    except Exception:
        pass

async def _ensure_client_connected(client, s_string, phone):
    if client is None:
        client = await _build_telethon_client(s_string)
    if not client.is_connected():
        await asyncio.wait_for(client.connect(), timeout=20)

    last_exc = None
    for _ in range(3):
        try:
            me = await asyncio.wait_for(client.get_me(), timeout=15)
            if me is not None:
                return client
        except (AuthKeyUnregisteredError, UserDeactivatedError, UserDeactivatedBanError) as auth_err:
            await _safe_disconnect(client)
            raise RuntimeError(f"Oturum yetkisiz ({phone}): {auth_err}")
        except Exception as exc:
            last_exc = exc
            await asyncio.sleep(1.5)

    try:
        await asyncio.wait_for(client.get_messages("me", limit=1), timeout=15)
        return client
    except (AuthKeyUnregisteredError, UserDeactivatedError, UserDeactivatedBanError) as auth_err:
        await _safe_disconnect(client)
        raise RuntimeError(f"Oturum yetkisiz ({phone}): {auth_err}")
    except Exception as exc:
        last_exc = exc

    try:
        authorized = await asyncio.wait_for(client.is_user_authorized(), timeout=12)
        if authorized:
            return client
    except (AuthKeyUnregisteredError, UserDeactivatedError, UserDeactivatedBanError) as auth_err:
        await _safe_disconnect(client)
        raise RuntimeError(f"Oturum yetkisiz ({phone}): {auth_err}")
    except Exception as exc:
        last_exc = exc

    # Ağ/zaman aşımı durumlarında oturumu hemen "yetkisiz" diye silmeden tekrar denemeye izin ver
    await _safe_disconnect(client)
    raise RuntimeError(f"Geçici bağlantı/doğrulama zaman aşımı ({phone}): {last_exc}")

async def _send_single_message(client, s_string, phone, group_id, ad_text, ad_entities):
    client = await _ensure_client_connected(client, s_string, phone)
    payload_text = ad_text if ad_entities else clean_bold_stars(ad_text)
    kwargs = {"formatting_entities": ad_entities, "parse_mode": None} if ad_entities else {"parse_mode": None}
    try:
        await asyncio.wait_for(client.send_message(group_id, payload_text, **kwargs), timeout=20)
        return client
    except Exception:
        await _safe_disconnect(client)
        client = await _ensure_client_connected(None, s_string, phone)
        await asyncio.wait_for(client.send_message(group_id, payload_text, **kwargs), timeout=20)
        return client

async def _sleep_cancellable(seconds, uid, task_key, target_s_name):
    if seconds <= 0:
        return
    remaining = seconds
    check_step = 5
    while remaining > 0:
        if task_key not in tasks:
            break
        if not target_s_name and remaining % 10 == 0:
            u_info = await asyncio.to_thread(db.get_user, uid)
            if (u_info or {}).get("is_running", 0) == 0:
                break
        step = min(check_step, remaining)
        await asyncio.sleep(step)
        remaining -= step

async def sender_loop(uid, context, chat_id, target_s_name=None):
    task_key = f"{uid}_auto_all_accounts" if not target_s_name else f"{uid}_acc_{target_s_name}"
    active_clients = {}

    try:
        while True:
            valid = await asyncio.to_thread(has_valid_access, uid)
            if not valid:
                if not target_s_name:
                    await asyncio.to_thread(db.set_user, uid, is_running=0)
                break

            user_data = await asyncio.to_thread(db.get_user, uid)
            if not target_s_name and (user_data or {}).get("is_running", 0) == 0:
                break
            if task_key not in tasks:
                break

            db_sessions = await asyncio.to_thread(db.get_user_sessions_db, uid)
            if target_s_name:
                active_sessions = [
                    (s[0], s[1], s[2]) for s in (db_sessions or [])
                    if s[0] == target_s_name and s[2] and len(s[2].strip()) > 20
                ]
            else:
                active_sessions = [
                    (s[0], s[1], s[2]) for s in (db_sessions or [])
                    if s[2] and len(s[2].strip()) > 20
                ]

            if not active_sessions:
                await asyncio.sleep(15)
                if target_s_name:
                    break
                continue

            for s_name, phone, s_string in active_sessions:
                if task_key not in tasks:
                    break
                if not target_s_name:
                    u_chk = await asyncio.to_thread(db.get_user, uid)
                    if (u_chk or {}).get("is_running", 0) == 0:
                        break

                client = active_clients.get(s_name)
                try:
                    client = await _ensure_client_connected(client, s_string, phone)
                    active_clients[s_name] = client
                except Exception as e:
                    log.error(f"Client bağlantı hatası ({phone}): {e}")
                    await _safe_disconnect(client)
                    active_clients.pop(s_name, None)
                    continue

                custom_text = await asyncio.to_thread(db.get_account_custom_text, s_name)
                ad_text = ""
                ad_entities = None

                if custom_text:
                    ad_text = clean_bold_stars(custom_text)
                else:
                    try:
                        messages = await asyncio.wait_for(client.get_messages("me", limit=1), timeout=15)
                        if messages and messages[0]:
                            msg = messages[0]
                            ad_text = getattr(msg, 'text', '') or getattr(msg, 'message', '')
                            ad_entities = getattr(msg, 'entities', None)
                    except Exception as e:
                        log.warning(f"Kayıtlı mesaj alınamadı ({phone}): {e}")

                if not ad_text:
                    continue

                try:
                    groups = []
                    async for dialog in client.iter_dialogs():
                        if dialog.is_group or (dialog.is_channel and getattr(dialog.entity, 'megagroup', False)):
                            groups.append(dialog)
                except Exception as e:
                    log.error(f"Grup listesi alınamadı ({phone}): {e}")
                    await _safe_disconnect(client)
                    active_clients.pop(s_name, None)
                    continue

                if not groups:
                    continue

                user_bl = blacklisted_groups.get(uid, set())
                acc_bl_raw = await asyncio.to_thread(db.get_account_blacklist_db, s_name)
                acc_bl_ids = set(acc_bl_raw or [])
                success_count, fail_count, skipped_count = 0, 0, 0
                failed_group_list = []

                for group in groups:
                    if task_key not in tasks:
                        break
                    if not target_s_name:
                        u_chk2 = await asyncio.to_thread(db.get_user, uid)
                        if (u_chk2 or {}).get("is_running", 0) == 0:
                            break

                    # Her gruptan önce güncel süreyi veritabanından oku
                    group_interval = int(await asyncio.to_thread(db.get_account_delay, s_name) or 10)

                    g_id, g_title = str(group.id), getattr(group, 'title', 'Grup')
                    entity = getattr(group, 'entity', None)
                    g_username = f"@{entity.username}".lower() if entity and getattr(entity, 'username', None) else ""

                    if g_id in user_bl or (g_username and g_username in user_bl) or g_id in acc_bl_ids:
                        skipped_count += 1
                        continue

                    try:
                        client = await _send_single_message(client, s_string, phone, group.id, ad_text, ad_entities)
                        active_clients[s_name] = client
                        success_count += 1
                    except FloodWaitError as exc:
                        log.warning(f"FloodWait ({phone}): {exc.seconds}s bekleniyor.")
                        try:
                            await context.bot.send_message(
                                chat_id=chat_id,
                                text=f"⚠️ **Akıllı Koruma Uyarısı ({phone}):** {exc.seconds} saniye bekleme."
                            )
                        except Exception:
                            pass

                        await _sleep_cancellable(exc.seconds, uid, task_key, target_s_name)
                        try:
                            client = await _send_single_message(client, s_string, phone, group.id, ad_text, ad_entities)
                            active_clients[s_name] = client
                            success_count += 1
                        except Exception:
                            fail_count += 1
                            failed_group_list.append(g_title)
                    except Exception as exc:
                        log.error(f"Mesaj gönderim hatası ({g_title}): {exc}")
                        fail_count += 1
                        failed_group_list.append(g_title)

                    if group_interval > 0:
                        await _sleep_cancellable(group_interval, uid, task_key, target_s_name)

                failed_reports_cache[s_name] = failed_group_list
                loop_interval = int(await asyncio.to_thread(db.get_account_loop, s_name) or 300)

                report_text = (
                    f"📊 **[{phone}] REKLAM TURU TAMAMLANDI**\n"
                    f"✅ Başarılı: {success_count} | ❌ Hatalı: {fail_count} | 🚫 Atlanan: {skipped_count}\n"
                    f"⏳ *Sonraki tur {loop_interval} saniye sonra başlayacak.*"
                )
                try:
                    await context.bot.send_message(chat_id=chat_id, text=report_text, parse_mode="Markdown")
                except Exception:
                    pass

                if task_key not in tasks:
                    break
                if not target_s_name:
                    u_chk3 = await asyncio.to_thread(db.get_user, uid)
                    if (u_chk3 or {}).get("is_running", 0) == 0:
                        break

                await _safe_disconnect(active_clients.pop(s_name, None))
                await _sleep_cancellable(loop_interval, uid, task_key, target_s_name)

            if target_s_name:
                break

    except asyncio.CancelledError:
        pass
    except Exception as e:
        log.exception("Döngü kritik hata: %s", e)
        await asyncio.sleep(5)
    finally:
        for _, c in list(active_clients.items()):
            await _safe_disconnect(c)
        active_clients.clear()
        if tasks.get(task_key) is asyncio.current_task():
            tasks.pop(task_key, None)

async def stop_sender(uid, chat_id, context, target_s_name=None):
    if not target_s_name:
        await asyncio.to_thread(db.set_user, uid, is_running=0)

    task_key = f"{uid}_auto_all_accounts" if not target_s_name else f"{uid}_acc_{target_s_name}"
    task = tasks.pop(task_key, None)
    if task and not task.done():
        task.cancel()
    try:
        await context.bot.send_message(chat_id=chat_id, text="⏹ **Reklam otomasyonu güvenli bir şekilde durduruldu.**")
    except Exception:
        pass

async def resume_active_users_background(application):
    await asyncio.sleep(3)
    try:
        running_users = await asyncio.to_thread(db.get_all_running_users)
        for uid in (running_users or []):
            valid = await asyncio.to_thread(db.license_valid, uid, ADMIN_ID)
            if valid:
                task_key = f"{uid}_auto_all_accounts"
                existing = tasks.get(task_key)
                if existing and not existing.done():
                    continue
                try:
                    class DummyContext:
                        def __init__(self, bot):
                            self.bot = bot
                    tasks[task_key] = asyncio.create_task(
                        sender_loop(uid, DummyContext(application.bot), uid)
                    )
                    log.info(f"Kullanıcı {uid} otomatik olarak kaldığı yerden başlatıldı.")
                except Exception as e:
                    log.error(f"Kullanıcı {uid} başlatılamadı: {e}")
    except Exception as e:
        log.error(f"Background resume hatası: {e}")

async def post_init(application):
    asyncio.create_task(resume_active_users_background(application))

async def button(update, context):
    q = update.callback_query
    try:
        await q.answer()
    except Exception:
        pass
    uid, action = q.from_user.id, q.data
    asyncio.create_task(asyncio.to_thread(db.ensure_user, uid, q.from_user.username))

    if action == "home":
        await show_home(update, context)
    elif action == "guide_menu":
        await show_guide(update, context)
    elif action == "pricing_panel":
        text = (
            "💎 **ABONELİK VE FİYAT LİSTESİ**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "📅 **Günlük Paket:** 200 TL\n"
            "📆 **Haftalık Paket:** 400 TL\n"
            "🗓 **Aylık Paket:** 1000 TL\n\n"
            "Satın almak istediğiniz paketi seçin:"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("💳 Günlük (200 TL) Satın Al", callback_data="buy_daily")],
            [InlineKeyboardButton("💳 Haftalık (400 TL) Satın Al", callback_data="buy_weekly")],
            [InlineKeyboardButton("💳 Aylık (1000 TL) Satın Al", callback_data="buy_monthly")],
            [InlineKeyboardButton("🔑 KEY'im Var (Kod Gir)", callback_data="redeem")],
            [InlineKeyboardButton("⬅ Ana Menü", callback_data="home")]
        ])
        await q.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)
    elif action in ["buy_daily", "buy_weekly", "buy_monthly"]:
        p_name, p_price, days = (
            ("Günlük Paket", "200 TL", 1)
            if action == "buy_daily"
            else (("Haftalık Paket", "400 TL", 7) if action == "buy_weekly" else ("Aylık Paket", "1000 TL", 30))
        )
        context.user_data["pending_buy_days"] = days
        context.user_data["pending_buy_name"] = p_name

        pay_text = (
            f"💳 **ÖDEME BİLDİRİMİ VE DETAYLARI**\n"
            f"📦 Paket: **{p_name}** | Tutar: **{p_price}**\n\n"
            f"🏦 **İBAN:** `TR100006200091000006969709`\n"
            f"• **Alıcı:** Garanti Ödeme ve Elektronik Para Hizmetleri A.Ş.\n"
            f"• **Açıklama:** `TAMİ7696919784875077`"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📤 Dekont Gönder", callback_data="send_receipt_prompt")],
            [InlineKeyboardButton("⬅ Geri", callback_data="pricing_panel")]
        ])
        await q.edit_message_text(pay_text, parse_mode="Markdown", reply_markup=kb)
    elif action == "send_receipt_prompt":
        context.user_data["flow"] = "awaiting_receipt"
        await q.edit_message_text("📤 Lütfen yaptığınız ödemenin **dekont fotoğrafını** bota doğrudan gönderin:", reply_markup=sub_menu_keyboard())
    elif action == "add":
        if not await asyncio.to_thread(has_valid_access, uid):
            await q.edit_message_text("❌ Aktif lisansınız yok.", reply_markup=sub_menu_keyboard())
            return
        context.user_data["flow"] = "phone"
        await q.edit_message_text("➕ Telefon numaranızı +ülke kodu ile gönderin (`+90...`):", parse_mode="Markdown", reply_markup=sub_menu_keyboard())
    elif action == "accounts":
        db_sessions = await asyncio.to_thread(db.get_user_sessions_db, uid)
        if not db_sessions:
            await q.edit_message_text("👤 Kayıtlı hesabınız yok.", reply_markup=get_keyboard(uid))
            return
        rows = []
        for s_name, phone, _ in db_sessions:
            uname = await asyncio.to_thread(db.get_account_username, s_name)
            display_str = f"@{uname} | {phone}" if uname else f"Kullanıcı Adı Yok | {phone}"
            rows.append([InlineKeyboardButton(f"📱 {display_str}", callback_data=f"manage_{s_name}")])
        rows.append([InlineKeyboardButton("⬅ Ana Menü", callback_data="home")])
        await q.edit_message_text("👤 **HESAPLARIM VE KULLANICI ADLARI**", reply_markup=InlineKeyboardMarkup(rows))
    elif action.startswith("manage_"):
        s_name = action.replace("manage_", "")
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("▶ Başlat", callback_data=f"start_one_{s_name}"), InlineKeyboardButton("⏹ Durdur", callback_data=f"stop_one_{s_name}")],
            [InlineKeyboardButton("✍️ Bu Hesaba Özel Metin Gir", callback_data=f"set_acc_text_{s_name}")],
            [InlineKeyboardButton("⏱ Süreleri Ayarla", callback_data=f"set_acc_times_{s_name}")],
            [InlineKeyboardButton("🚫 Gruba Özel Engel", callback_data=f"acc_bl_{s_name}")],
            [InlineKeyboardButton("❌ Hesabı Sil", callback_data=f"del_{s_name}")],
            [InlineKeyboardButton("⬅ Geri", callback_data="accounts")]
        ])
        await q.edit_message_text("📱 **Hesap Yönetim Paneli**", reply_markup=kb)
    elif action == "account_config_menu":
        db_sessions = await asyncio.to_thread(db.get_user_sessions_db, uid)
        if not db_sessions:
            await q.edit_message_text("❌ Yapılandırılacak hesabınız yok.", reply_markup=get_keyboard(uid))
            return
        rows = []
        for s_name, phone, _ in db_sessions:
            uname = await asyncio.to_thread(db.get_account_username, s_name)
            disp = f"@{uname}" if uname else phone
            rows.append([InlineKeyboardButton(f"⚙️ Ayarla: {disp}", callback_data=f"manage_{s_name}")])
        rows.append([InlineKeyboardButton("⬅ Ana Menü", callback_data="home")])
        await q.edit_message_text("📩 **HESABA ÖZEL METİN VE SÜRE YAPILANDIRMASI**\n\nİşlem yapmak istediğiniz hesabı seçin:", reply_markup=InlineKeyboardMarkup(rows))
    elif action.startswith("start_one_"):
        s_name = action.replace("start_one_", "")
        await start_sender(uid, q.message.chat_id, context, target_s_name=s_name, force_restart=True)
        await q.message.reply_text("▶ **Seçilen hesap için reklam başlatıldı! (Sürekli otomatik döngüde çalışacak)**")
    elif action.startswith("stop_one_"):
        s_name = action.replace("stop_one_", "")
        await stop_sender(uid, q.message.chat_id, context, target_s_name=s_name)
    elif action.startswith("set_acc_text_"):
        s_name = action.replace("set_acc_text_", "")
        context.user_data["flow"] = f"acc_text_input_{s_name}"
        await q.edit_message_text(
            "✍️ Bu hesabın göndereceği **özel reklam metnini** yazın.\n\n"
            "💡 _Not: Kayıtlı Mesajlar kullanmak istiyorsanız `sil` yazın._",
            reply_markup=sub_menu_keyboard()
        )
    elif action.startswith("set_acc_times_"):
        s_name = action.replace("set_acc_times_", "")
        context.user_data["flow"] = f"acc_times_delay_{s_name}"
        await q.edit_message_text("⏱ Bu hesap için **grup arası bekleme süresini (saniye)** yazın (Örn: `10`):", reply_markup=sub_menu_keyboard())
    elif action == "start_stop_menu":
        db_sessions = await asyncio.to_thread(db.get_user_sessions_db, uid)
        if not db_sessions:
            await q.edit_message_text("❌ Yönetilecek hesabınız yok.", reply_markup=get_keyboard(uid))
            return
        rows = []
        for s_name, phone, _ in db_sessions:
            uname = await asyncio.to_thread(db.get_account_username, s_name)
            disp = f"@{uname}" if uname else phone
            rows.append([
                InlineKeyboardButton(f"▶ Başlat: {disp}", callback_data=f"start_one_{s_name}"),
                InlineKeyboardButton(f"⏹ Durdur: {disp}", callback_data=f"stop_one_{s_name}")
            ])
        rows.append([InlineKeyboardButton("⬅ Ana Menü", callback_data="home")])
        await q.edit_message_text("▶️ **ANA PANELDEN BAŞLAT / DURDUR KONTROLÜ**", reply_markup=InlineKeyboardMarkup(rows))
    elif action == "failed_reports":
        db_sessions = await asyncio.to_thread(db.get_user_sessions_db, uid)
        report_lines = ["📊 **BAŞARISIZ GÖNDERİM RAPORLARI**\n"]
        for s_name, phone, _ in (db_sessions or []):
            fails = failed_reports_cache.get(s_name, [])
            report_lines.append(f"📱 `{phone}` -> Başarısız Grup Sayısı: {len(fails)}")
            if fails:
                report_lines.append(f"  • Hata Alınanlar: {', '.join(fails[:5])}")
        await q.edit_message_text("\n".join(report_lines), parse_mode="Markdown", reply_markup=sub_menu_keyboard())
    elif action.startswith("acc_bl_"):
        s_name = action.replace("acc_bl_", "")
        context.user_data["flow"] = f"acc_blacklist_add_{s_name}"
        await q.edit_message_text("🚫 Bu hesabın mesaj atmayacağı grubun linkini veya kullanıcı adını gönderin:", reply_markup=sub_menu_keyboard())
    elif action.startswith("del_"):
        s_name = action.replace("del_", "")
        await asyncio.to_thread(db.delete_session_db, s_name)
        await _safe_disconnect(clients.pop(s_name, None))
        await q.edit_message_text("✅ Hesap silindi.", reply_markup=get_keyboard(uid))
    elif action == "blacklist":
        context.user_data["flow"] = "blacklist"
        await q.edit_message_text("🚫 Genel engellenecek grup linkini veya kullanıcı adını gönderin:", reply_markup=sub_menu_keyboard())
    elif action == "redeem":
        context.user_data["flow"] = "redeem"
        await q.edit_message_text("🔑 KEY kodunu girin:", reply_markup=sub_menu_keyboard())
    elif action == "admin" and is_admin(uid):
        await q.edit_message_text("👑 **YÖNETİCİ LİSANS & İSİMLİ KEY ÜRETİM PANELİ**", reply_markup=admin_keyboard())
    elif action == "key_create" and is_admin(uid):
        context.user_data["flow"] = "key_note_and_days"
        await q.edit_message_text(
            "➕ **İsimli KEY Üretimi**\n\nFormat: `İsim,GünSayısı` (Örn: `Ahmet Bey,30`)",
            parse_mode="Markdown",
            reply_markup=admin_keyboard()
        )
    elif action == "key_list" and is_admin(uid):
        await admin_panel_refresh(q)
    elif action.startswith("rev_key_") and is_admin(uid):
        k_code = action.replace("rev_key_", "")
        await asyncio.to_thread(db.revoke_license, k_code)
        try:
            await q.answer(f"🔑 Key iptal edildi: {k_code}", show_alert=True)
        except Exception:
            pass
        await admin_panel_refresh(q)
    elif action == "admin_broadcast" and is_admin(uid):
        context.user_data["flow"] = "admin_broadcast_msg"
        await q.edit_message_text("📢 Tüm kullanıcılara duyuru mesajını yazın:", reply_markup=admin_keyboard())
    elif action.startswith("approve_pay_") and is_admin(uid):
        target_uid = int(action.replace("approve_pay_", ""))
        await asyncio.to_thread(db.add_license_time_db, target_uid, 30)
        await q.edit_message_text("✅ Ödeme onaylandı.")
        try:
            await context.bot.send_message(chat_id=target_uid, text="🎉 Ödemeniz onaylandı!")
        except Exception:
            pass
    elif action.startswith("reject_pay_") and is_admin(uid):
        target_uid = int(action.replace("reject_pay_", ""))
        await q.edit_message_text("❌ Ödeme reddedildi.")
        try:
            await context.bot.send_message(chat_id=target_uid, text="❌ Ödemeniz onaylanmadı.")
        except Exception:
            pass

async def admin_panel_refresh(q):
    rows = await asyncio.to_thread(db.list_licenses)
    kb_list = []
    for r in (rows or []):
        code = r['code']
        note = r.get('note', 'İsimsiz')
        status_icon = "🟢 Aktif" if not r['is_used'] else "🔴 Kullanıldı"
        kb_list.append([
            InlineKeyboardButton(f"{note} | {code} ({r['duration_days']}g) - {status_icon}", callback_data="ignore"),
            InlineKeyboardButton("🗑 İptal Et", callback_data=f"rev_key_{code}")
        ])
    kb_list.append([InlineKeyboardButton("⬅ Ana Menü", callback_data="home")])
    await q.edit_message_text("📋 **KEY LİSTESİ VE SAHİPLERİ**", reply_markup=InlineKeyboardMarkup(kb_list))

async def text_handler(update, context):
    uid = update.effective_user.id
    text = update.effective_message.text.strip() if update.effective_message.text else ""
    flow = context.user_data.get("flow")

    if update.effective_message.photo and flow == "awaiting_receipt":
        photo = update.effective_message.photo[-1]
        context.user_data.clear()
        await update.effective_message.reply_text("✅ Dekont yöneticiye iletildi.", reply_markup=get_keyboard(uid))
        kb_admin = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Onayla", callback_data=f"approve_pay_{uid}"), InlineKeyboardButton("❌ Reddet", callback_data=f"reject_pay_{uid}")]
        ])
        try:
            await context.bot.send_photo(chat_id=ADMIN_ID, photo=photo.file_id, caption=f"🚨 **ÖDEME TALEBİ**\nKullanıcı: `{uid}`", reply_markup=kb_admin)
        except Exception:
            pass
        return

    if not flow:
        return

    if flow.startswith("acc_text_input_"):
        s_name = flow.replace("acc_text_input_", "")
        if text.lower() == "sil":
            await asyncio.to_thread(db.set_account_custom_text, s_name, "")
            await update.effective_message.reply_text("✅ Özel metin kaldırıldı (Kayıtlı Mesajlara dönüldü).", reply_markup=get_keyboard(uid))
        else:
            await asyncio.to_thread(db.set_account_custom_text, s_name, clean_bold_stars(text))
            await update.effective_message.reply_text("✅ Özel reklam metni kaydedildi!", reply_markup=get_keyboard(uid))
        context.user_data.clear()
        return

    if flow.startswith("acc_times_delay_"):
        s_name = flow.replace("acc_times_delay_", "")
        try:
            val = int(text)
            await asyncio.to_thread(db.set_account_delay, s_name, val)
            context.user_data["flow"] = f"acc_times_loop_{s_name}"
            await update.effective_message.reply_text("⏱ Şimdi bu hesap için **Tur Dinlenme Süresini (saniye)** yazın (Örn: `600`):", reply_markup=sub_menu_keyboard())
        except ValueError:
            await update.effective_message.reply_text("Lütfen geçerli bir sayı girin.")
        return

    if flow.startswith("acc_times_loop_"):
        s_name = flow.replace("acc_times_loop_", "")
        try:
            val = int(text)
            await asyncio.to_thread(db.set_account_loop, s_name, val)
            context.user_data.clear()
            acc_task_key = f"{uid}_acc_{s_name}"
            if acc_task_key in tasks and not tasks[acc_task_key].done():
                await start_sender(uid, update.effective_message.chat_id, context, target_s_name=s_name, force_restart=True)
            await update.effective_message.reply_text(f"✅ Tur dinlenme süresi {val} saniye olarak ayarlandı!", reply_markup=get_keyboard(uid))
        except ValueError:
            await update.effective_message.reply_text("Lütfen geçerli bir sayı girin.")
        return

    if flow == "key_note_and_days" and is_admin(uid):
        try:
            parts = text.split(",")
            if len(parts) < 2:
                await update.effective_message.reply_text("❌ Hatalı format! `İsim,GünSayısı` şeklinde yazın.")
                return
            note = parts[0].strip()
            days = int(parts[1].strip())
            code = "FKD-" + "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(10))
            await asyncio.to_thread(db.create_license, code, days, note=note)
            context.user_data.clear()
            await update.effective_message.reply_text(f"✅ **KEY Üretildi:** `{code}` ({note} - {days} Gün)", parse_mode="Markdown", reply_markup=admin_keyboard())
        except ValueError:
            await update.effective_message.reply_text("❌ Gün sayısı sayı olmalıdır.")
        return

    if flow == "admin_broadcast_msg" and is_admin(uid):
        users = await asyncio.to_thread(db.get_all_users)
        for u_id in (users or []):
            try:
                await context.bot.send_message(chat_id=u_id, text=f"📢 **DUYURU**\n\n{text}", parse_mode="Markdown")
            except Exception:
                pass
        context.user_data.clear()
        await update.effective_message.reply_text("✅ Duyuru iletildi.", reply_markup=admin_keyboard())
        return

    if flow == "phone":
        if not re.fullmatch(r"\+[1-9]\d{7,14}", text):
            await update.effective_message.reply_text("Geçerli numara girin (+90...)")
            return
        old_pending = pending.pop(uid, None)
        if old_pending:
            await _safe_disconnect(old_pending.get("client"))
        session_name = f"SESSION_{uid}_{text}"
        client = TelegramClient(
            StringSession(),
            API_ID,
            API_HASH,
            connection_retries=3,
            auto_reconnect=False,
            timeout=20
        )
        await client.connect()
        try:
            sent = await client.send_code_request(text)
            pending[uid] = {"client": client, "phone": text, "hash": sent.phone_code_hash, "session_name": session_name}
            context.user_data["flow"] = "code"
            await update.effective_message.reply_text("📲 Telegram kodunu girin:")
        except Exception as e:
            await _safe_disconnect(client)
            await update.effective_message.reply_text(f"Hata: {e}")

    elif flow == "code":
        item = pending.get(uid)
        if not item:
            return
        try:
            await item["client"].sign_in(phone=item["phone"], code=text, phone_code_hash=item["hash"])
            s_name = item["session_name"]
            s_string = item["client"].session.save()
            me_user = await item["client"].get_me()
            username = me_user.username if me_user and me_user.username else ""
            await _safe_disconnect(item["client"])
            clients.pop(s_name, None)
            await asyncio.to_thread(db.save_session_db, uid, s_name, item["phone"], s_string, username=username)
            pending.pop(uid, None)
            context.user_data.clear()
            await update.effective_message.reply_text("✅ Hesap bağlandı!", reply_markup=get_keyboard(uid))
        except SessionPasswordNeededError:
            context.user_data["flow"] = "2fa"
            await update.effective_message.reply_text("🔒 2FA şifrenizi girin:")
        except Exception as e:
            await update.effective_message.reply_text(f"Hata: {e}")

    elif flow == "2fa":
        item = pending.get(uid)
        if not item:
            return
        try:
            await item["client"].sign_in(password=text)
            s_name = item["session_name"]
            s_string = item["client"].session.save()
            me_user = await item["client"].get_me()
            username = me_user.username if me_user and me_user.username else ""
            await _safe_disconnect(item["client"])
            clients.pop(s_name, None)
            await asyncio.to_thread(db.save_session_db, uid, s_name, item["phone"], s_string, username=username)
            pending.pop(uid, None)
            context.user_data.clear()
            await update.effective_message.reply_text("✅ Hesap bağlandı!", reply_markup=get_keyboard(uid))
        except Exception as e:
            await update.effective_message.reply_text(f"Hata: {e}")

    elif flow.startswith("acc_blacklist_add_"):
        s_name = flow.replace("acc_blacklist_add_", "")
        target = text.replace("https://t.me/", "").replace("t.me/", "").strip().lstrip("@").lower()
        await asyncio.to_thread(db.add_account_blacklist_db, s_name, target)
        context.user_data.clear()
        await update.effective_message.reply_text(f"✅ Engellendi: {target}", reply_markup=get_keyboard(uid))

    elif flow == "blacklist":
        target = "@" + text.replace("https://t.me/", "").replace("t.me/", "").strip().lstrip("@").lower()
        if uid not in blacklisted_groups:
            blacklisted_groups[uid] = set()
        blacklisted_groups[uid].add(target)
        context.user_data.clear()
        await update.effective_message.reply_text(f"✅ Engellendi: {target}", reply_markup=get_keyboard(uid))

    elif flow == "redeem":
        _, msg = await asyncio.to_thread(db.use_license, uid, text)
        context.user_data.clear()
        await update.effective_message.reply_text(msg, reply_markup=get_keyboard(uid))

def main():
    db.init_db()
    Thread(target=run_web, daemon=True).start()
    Thread(target=_keep_alive_worker, daemon=True).start()

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .connect_timeout(15)
        .read_timeout(20)
        .write_timeout(20)
        .pool_timeout(10)
        .build()
    )
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    app.add_handler(MessageHandler(filters.PHOTO, text_handler))
    app.run_polling(drop_pending_updates=True, poll_interval=0.5, timeout=15)

if __name__ == "__main__":
    main()
