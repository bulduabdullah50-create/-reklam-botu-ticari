import os
import re
import time
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
    PhoneNumberBannedError,
    PhoneCodeInvalidError,
    PhoneCodeExpiredError,
    AuthKeyUnregisteredError,
    UserDeactivatedError,
    UserDeactivatedBanError,
    ChatWriteForbiddenError,
)
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)
import database as db

app_web = Flask(__name__)


@app_web.route("/")
def home_web():
    return "💎 FARKETMEZDA PLATINUM REKLAM BOTU AKTİF!"


@app_web.route("/health")
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
            time.sleep(240)
            req = urllib.request.Request(target_url, method="GET")
            with urllib.request.urlopen(req, timeout=5):
                pass
        except Exception:
            pass


logging.basicConfig(level=logging.INFO)
log = logging.getLogger("farketmezda")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logging.getLogger("telethon.network.mtprotosender").setLevel(logging.WARNING)
logging.getLogger("telethon.client.updates").setLevel(logging.ERROR)

BOT_TOKEN = os.environ["BOT_TOKEN"]
API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
ADMIN_ID = int(os.environ["ADMIN_ID"])

clients, pending, tasks, blacklisted_groups, failed_reports_cache = {}, {}, {}, {}, {}
restricted_groups_by_account: dict[str, dict[str, dict[str, str]]] = {}
_active_single_accounts: set[str] = set()
_explicitly_stopped_users: set[int] = set()


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


def _sanitize_notify_text(raw: str) -> str:
    txt = str(raw or "").replace("`", "'").replace("*", "").replace("_", "-")
    return txt[:600]


def _is_connection_drop_error(err: Exception) -> bool:
    if isinstance(err, (ConnectionError, TimeoutError, OSError, asyncio.TimeoutError)):
        return True
    msg = str(err or "").lower()
    return any(
        token in msg
        for token in (
            "server closed the connection",
            "connection closed",
            "receiving data",
            "disconnected",
            "connection reset",
            "broken pipe",
            "timed out",
            "timeout",
            "cannot send requests while disconnected",
        )
    )


def _is_write_restricted_error(err: Exception) -> bool:
    if isinstance(err, ChatWriteForbiddenError):
        return True
    msg = str(err or "").lower()
    return any(
        token in msg
        for token in (
            "chatwriteforbidden",
            "you can't write in this chat",
            "write is forbidden",
            "user is banned in the channel",
            "chat_send_plain_forbidden",
            "chat_guest_send_forbidden",
            "channel_private",
        )
    )


def _add_restricted_group(s_name: str, group_id: str, title: str, reason: str):
    acc_map = restricted_groups_by_account.setdefault(s_name, {})
    acc_map[str(group_id)] = {
        "title": title or str(group_id),
        "reason": _sanitize_notify_text(reason),
    }


def _remove_restricted_group(s_name: str, group_id: str) -> bool:
    acc_map = restricted_groups_by_account.get(s_name)
    if not acc_map:
        return False
    return acc_map.pop(str(group_id), None) is not None


def _set_single_account_state(s_name: str, active: bool):
    if active:
        _active_single_accounts.add(s_name)
    else:
        _active_single_accounts.discard(s_name)
    for fn_name in ("set_account_running", "set_session_running"):
        fn = getattr(db, fn_name, None)
        if callable(fn):
            try:
                fn(s_name, 1 if active else 0)
                return
            except Exception:
                pass


def _get_running_single_accounts_for_user(uid: int) -> list[str]:
    found = set()
    for fn_name in ("get_running_sessions_db", "get_running_accounts_db", "get_active_sessions_db"):
        fn = getattr(db, fn_name, None)
        if callable(fn):
            try:
                rows = fn(uid) or []
                for r in rows:
                    if isinstance(r, (tuple, list)) and r:
                        found.add(str(r[0]))
                    elif isinstance(r, dict) and r.get("session_name"):
                        found.add(str(r["session_name"]))
                    elif isinstance(r, str):
                        found.add(r)
            except Exception:
                pass
    try:
        user_sessions = db.get_user_sessions_db(uid) or []
        for row in user_sessions:
            s_name = str(row[0])
            if s_name in _active_single_accounts:
                found.add(s_name)
            elif len(row) >= 4 and row[3] in (1, True, "1"):
                found.add(s_name)
    except Exception:
        pass
    return list(found)


async def _notify_issue(context, chat_id, message: str):
    if not context or not chat_id:
        return
    try:
        await context.bot.send_message(chat_id=chat_id, text=message[:3900])
    except Exception:
        pass


async def _notify_error(
    context,
    chat_id,
    stage: str,
    exc: Exception,
    action_note: str = "Reklama kaldığı yerden devam ediliyor.",
):
    err_type = type(exc).__name__ if exc else "Error"
    err_detail = _sanitize_notify_text(exc)
    msg = (
        f"⚠️ ŞU SORUN ÇIKTI ({_sanitize_notify_text(stage)}):\n"
        f"Hata: {err_type}\n"
        f"Detay: {err_detail}\n"
        f"Durum: {_sanitize_notify_text(action_note)}"
    )
    await _notify_issue(context, chat_id, msg)


def get_keyboard(uid):
    rows = [
        [InlineKeyboardButton("💎 ABONELİK & FİYATLAR", callback_data="pricing_panel")],
        [
            InlineKeyboardButton("➕ Hesap Ekle", callback_data="add"),
            InlineKeyboardButton("👤 Hesaplarım", callback_data="accounts"),
        ],
        [InlineKeyboardButton("▶️ Başlat / ⏹ Durdur", callback_data="start_stop_menu")],
        [InlineKeyboardButton("📩 Hesaba Özel Metin & Süreler", callback_data="account_config_menu")],
        [InlineKeyboardButton("🚧 Yazılamayan / Muteli Gruplar", callback_data="restricted_groups_menu")],
        [InlineKeyboardButton("📊 Başarısız Raporlar", callback_data="failed_reports")],
        [InlineKeyboardButton("📖 Kurulum & Ödeme Rehberi", callback_data="guide_menu")],
        [
            InlineKeyboardButton("🚫 Genel Grup Engel", callback_data="blacklist"),
            InlineKeyboardButton("🔑 KEY Aktif Et", callback_data="redeem"),
        ],
        [InlineKeyboardButton("💬 Canlı Destek", url="https://t.me/farketmezda")],
    ]
    if is_admin(uid):
        rows.append([InlineKeyboardButton("👑 YÖNETİCİ LİSANS İPTAL & İSİMLİ KEY ÜRET", callback_data="admin")])
    return InlineKeyboardMarkup(rows)


def sub_menu_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅ Geri / Ana Menü", callback_data="home")]])


def admin_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("➕ İsimli KEY Üret", callback_data="key_create"),
            InlineKeyboardButton("📋 KEY Listesi & Sahipleri", callback_data="key_list"),
        ],
        [InlineKeyboardButton("📢 Duyuru Geç", callback_data="admin_broadcast")],
        [InlineKeyboardButton("⬅ Ana Menü", callback_data="home")],
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


async def unmute_group_command(update, context):
    uid = update.effective_user.id
    args = context.args or []
    query_txt = " ".join(args).strip().lower()
    db_sessions = await asyncio.to_thread(db.get_user_sessions_db, uid) or []
    s_names = [str(row[0]) for row in db_sessions]

    if not query_txt:
        await update.effective_message.reply_text(
            "ℹ️ Mute/engel kaldırmak için komutla birlikte grup ID veya grup adını yazın.\n"
            "Örnek: `/unmute_group -1001234567890` veya `/unmute_group Arayış Grubu`",
            parse_mode="Markdown",
        )
        return

    removed_items = []
    for s_name in s_names:
        acc_map = restricted_groups_by_account.get(s_name, {})
        for g_id, info in list(acc_map.items()):
            title_low = str(info.get("title", "")).lower()
            if query_txt == g_id.lower() or query_txt in title_low:
                _remove_restricted_group(s_name, g_id)
                removed_items.append(f"{info.get('title', g_id)} (`{g_id}`)")

    if removed_items:
        await update.effective_message.reply_text(
            "✅ **Mute/kısıt kaldırıldı, reklam listesine geri eklendi:**\n• " + "\n• ".join(removed_items[:15]),
            parse_mode="Markdown",
        )
    else:
        await update.effective_message.reply_text("⚠️ Yazılamayan gruplar listesinde eşleşen bir grup bulunamadı.")


async def show_guide(update, context):
    q = update.callback_query
    guide_text = (
        "📖 **FARKETMEZDA PLATINUM | KURULUM VE ÖDEME REHBERİ**\n\n"
        "> **1️⃣ Nasıl Satın Alınır ve Ödeme Nasıl Yapılır?**\n"
        "• Günlük 200 TL, Haftalık 400 TL, Aylık 1000 TL.\n"
        "• İletişim: **`@farketmezda`** 💬\n\n"
        "> **2️⃣ Bot Nasıl Kurulur?**\n"
        "• Lisans keyini girin, hesapları bağlayın, reklam metni/süre ayarlayın ve Başlat/Durdur menüsünden yönetin."
    )
    await q.edit_message_text(guide_text, parse_mode="Markdown", reply_markup=sub_menu_keyboard())


async def _cancel_task_immediately(task_key: str):
    task = tasks.pop(task_key, None)
    if task and not task.done():
        task.cancel()
        try:
            await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), timeout=2.5)
        except Exception:
            pass


async def _purge_user_stale_runs(uid: int, target_s_name: str | None = None):
    stale_keys = [k for k in list(tasks.keys()) if k.startswith(f"{uid}_")]
    for k in stale_keys:
        await _cancel_task_immediately(k)

    try:
        user_sessions = await asyncio.to_thread(db.get_user_sessions_db, uid)
        for s_name, _, _ in user_sessions or []:
            if not target_s_name or s_name == target_s_name:
                old_c = clients.pop(s_name, None)
                await _safe_disconnect(old_c)
    except Exception:
        pass


async def start_sender(uid, chat_id, context, target_s_name=None, force_restart=True):
    valid = await asyncio.to_thread(has_valid_access, uid)
    if not valid:
        return

    _explicitly_stopped_users.discard(int(uid))
    await _purge_user_stale_runs(uid, target_s_name=None)

    await asyncio.to_thread(db.set_user, uid, is_running=1)
    if not target_s_name:
        task_key = f"{uid}_auto_all_accounts"
    else:
        await asyncio.to_thread(_set_single_account_state, target_s_name, True)
        task_key = f"{uid}_acc_{target_s_name}"

    tasks[task_key] = asyncio.create_task(sender_loop(uid, context, chat_id, target_s_name))


async def _build_telethon_client(s_string):
    clean_string = (s_string or "").strip().strip('"').strip("'").replace("\n", "").strip()
    return TelegramClient(
        StringSession(clean_string),
        API_ID,
        API_HASH,
        device_model="iPhone 15 Pro",
        system_version="iOS 18.1",
        app_version="11.3",
        connection_retries=4,
        retry_delay=2,
        auto_reconnect=False,
        timeout=20,
    )


async def _safe_disconnect(client):
    if client is None:
        return
    try:
        coro = client.disconnect()
        if asyncio.iscoroutine(coro) or hasattr(coro, "__await__"):
            await asyncio.wait_for(coro, timeout=3)
    except Exception:
        pass


async def _verify_and_save_session(uid, s_name, phone, s_string):
    clean_string = (s_string or "").strip().strip('"').strip("'").replace("\n", "").strip()
    if not clean_string:
        raise RuntimeError("Oturum anahtarı boş oluştu.")

    verify_client = TelegramClient(
        StringSession(clean_string),
        API_ID,
        API_HASH,
        device_model="iPhone 15 Pro",
        system_version="iOS 18.1",
        app_version="11.3",
        connection_retries=3,
        retry_delay=2,
        auto_reconnect=False,
        timeout=20,
    )
    try:
        await asyncio.wait_for(verify_client.connect(), timeout=20)
        me_user = await asyncio.wait_for(verify_client.get_me(), timeout=15)
        if me_user is None:
            raise RuntimeError("Oturum doğrulaması tamamlanamadı.")
        final_string = verify_client.session.save() or clean_string
        username = me_user.username if me_user.username else ""
        await asyncio.to_thread(db.save_session_db, uid, s_name, phone, final_string, username=username)
    finally:
        await _safe_disconnect(verify_client)


async def kod_iste_ve_logla(client: TelegramClient, phone: str):
    try:
        sent = await asyncio.wait_for(client.send_code_request(phone), timeout=20)
        kod_tipi = type(getattr(sent, "type", None)).__name__
        timeout_sn = getattr(sent, "timeout", None)
        p_hash = getattr(sent, "phone_code_hash", None)
        print(f"[CODE_SENT] phone={phone} type={kod_tipi} timeout={timeout_sn}", flush=True)
        return True, kod_tipi, timeout_sn, p_hash
    except (FloodWaitError, PhoneNumberBannedError, SessionPasswordNeededError) as e:
        print(f"[CODE_ERR] phone={phone} err={type(e).__name__}: {e}", flush=True)
        return False, f"{type(e).__name__}: {e}", None, None
    except Exception as e:
        print(f"[CODE_ERR_UNEXPECTED] phone={phone} err={type(e).__name__}: {e}", flush=True)
        return False, f"{type(e).__name__}: {e}", None, None


async def request_login_code_with_diag(phone: str):
    clean_phone = re.sub(r"[^\d+]", "", str(phone or "").strip())
    if not clean_phone.startswith("+"):
        clean_phone = f"+{clean_phone}"

    temp_client = TelegramClient(
        StringSession(),
        API_ID,
        API_HASH,
        device_model="iPhone 15 Pro",
        system_version="iOS 18.1",
        app_version="11.3",
        connection_retries=3,
        retry_delay=2,
        auto_reconnect=False,
        timeout=20,
    )
    try:
        await asyncio.wait_for(temp_client.connect(), timeout=20)
        ok, kod_tipi_veya_hata, timeout_sn, p_hash = await kod_iste_ve_logla(temp_client, clean_phone)
        if not ok:
            await _safe_disconnect(temp_client)
            return False, None, clean_phone, None, f"Kod gönderilemedi ({kod_tipi_veya_hata})"
        msg = (
            f"✅ Kod isteği gönderildi.\n"
            f"• Kanal: `{kod_tipi_veya_hata}`\n"
            f"• Bekleme süresi: `{timeout_sn}` sn\n"
            f"Gelen kodu şimdi yazın:"
        )
        return True, temp_client, clean_phone, p_hash, msg
    except Exception as e:
        print(f"[SEND_CODE_ERR] phone={clean_phone} err={type(e).__name__}: {e}", flush=True)
        await _safe_disconnect(temp_client)
        return False, None, clean_phone, None, f"Kod isteği hatası ({type(e).__name__}): {e}"


async def confirm_login_code_and_save(
    uid: int,
    s_name: str,
    phone: str,
    temp_client: TelegramClient,
    phone_code_hash: str | None,
    code: str,
    password: str | None = None,
):
    clean_code = str(code or "").strip().replace(" ", "")
    try:
        if not temp_client.is_connected():
            await asyncio.wait_for(temp_client.connect(), timeout=20)
        if phone_code_hash:
            await asyncio.wait_for(
                temp_client.sign_in(phone=phone, code=clean_code, phone_code_hash=phone_code_hash),
                timeout=20,
            )
        else:
            await asyncio.wait_for(temp_client.sign_in(phone=phone, code=clean_code), timeout=20)

        session_str = temp_client.session.save()
        await _verify_and_save_session(uid, s_name, phone, session_str)
        await _safe_disconnect(temp_client)
        return True, None, "✅ Hesap başarıyla doğrulandı ve kaydedildi."
    except SessionPasswordNeededError:
        if not password:
            return False, "NEED_2FA", "🔐 İki adımlı doğrulama (2FA) şifrenizi girin:"
        try:
            await asyncio.wait_for(temp_client.sign_in(password=str(password).strip()), timeout=20)
            session_str = temp_client.session.save()
            await _verify_and_save_session(uid, s_name, phone, session_str)
            await _safe_disconnect(temp_client)
            return True, None, "✅ 2FA doğrulandı ve hesap başarıyla kaydedildi."
        except Exception as e2:
            await _safe_disconnect(temp_client)
            return False, "ERR", f"❌ 2FA giriş hatası: {e2}"
    except (PhoneCodeInvalidError, PhoneCodeExpiredError) as e:
        await _safe_disconnect(temp_client)
        return False, "ERR", f"❌ Kod geçersiz veya süresi dolmuş ({type(e).__name__})."
    except Exception as e:
        await _safe_disconnect(temp_client)
        return False, "ERR", f"❌ Giriş doğrulanamadı: {e}"


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
        except asyncio.CancelledError:
            await _safe_disconnect(client)
            raise
        except Exception as exc:
            last_exc = exc
            if not client.is_connected():
                try:
                    await asyncio.wait_for(client.connect(), timeout=20)
                except Exception:
                    pass
            await asyncio.sleep(1.0)

    try:
        await asyncio.wait_for(client.get_messages("me", limit=1), timeout=15)
        return client
    except (AuthKeyUnregisteredError, UserDeactivatedError, UserDeactivatedBanError) as auth_err:
        await _safe_disconnect(client)
        raise RuntimeError(f"Oturum yetkisiz ({phone}): {auth_err}")
    except asyncio.CancelledError:
        await _safe_disconnect(client)
        raise
    except Exception as exc:
        last_exc = exc

    try:
        authorized = await asyncio.wait_for(client.is_user_authorized(), timeout=12)
        if authorized:
            return client
    except (AuthKeyUnregisteredError, UserDeactivatedError, UserDeactivatedBanError) as auth_err:
        await _safe_disconnect(client)
        raise RuntimeError(f"Oturum yetkisiz ({phone}): {auth_err}")
    except asyncio.CancelledError:
        await _safe_disconnect(client)
        raise
    except Exception as exc:
        last_exc = exc

    await _safe_disconnect(client)
    raise RuntimeError(f"Geçici bağlantı/doğrulama zaman aşımı ({phone}): {last_exc}")


async def _send_single_message(client, s_string, phone, group_id, ad_text, ad_entities):
    client = await _ensure_client_connected(client, s_string, phone)
    payload_text = ad_text if ad_entities else clean_bold_stars(ad_text)
    kwargs = {"formatting_entities": ad_entities, "parse_mode": None} if ad_entities else {"parse_mode": None}
    try:
        await asyncio.wait_for(client.send_message(group_id, payload_text, **kwargs), timeout=12)
        return client
    except asyncio.CancelledError:
        raise
    except (FloodWaitError, ChatWriteForbiddenError):
        raise
    except Exception as exc:
        if _is_write_restricted_error(exc):
            raise
        if _is_connection_drop_error(exc):
            log.warning(f"Bağlantı koptu ({phone}), yeniden bağlanıp aynı grupta devam ediliyor: {exc}")
        await _safe_disconnect(client)
        await asyncio.sleep(2.0)
        client = await _ensure_client_connected(None, s_string, phone)
        await asyncio.wait_for(client.send_message(group_id, payload_text, **kwargs), timeout=12)
        return client


async def _sleep_exact_seconds(seconds, uid, task_key, target_s_name):
    total = max(0, int(seconds or 0))
    if total <= 0:
        return
    start_mono = time.monotonic()
    target_mono = start_mono + float(total)
    last_running_check = 0.0

    while True:
        if int(uid) in _explicitly_stopped_users:
            raise asyncio.CancelledError()
        if task_key not in tasks or tasks.get(task_key) is not asyncio.current_task():
            raise asyncio.CancelledError()

        now_mono = time.monotonic()
        if (now_mono - last_running_check) >= 2.0:
            last_running_check = now_mono
            u_info = await asyncio.to_thread(db.get_user, uid)
            if (u_info or {}).get("is_running", 0) == 0:
                raise asyncio.CancelledError()

        remaining = target_mono - time.monotonic()
        if remaining <= 0.0:
            break
        await asyncio.sleep(min(0.25, remaining))


async def _get_effective_delay(uid: int, s_name: str) -> int:
    val = await asyncio.to_thread(db.get_account_delay, s_name)
    if val is not None:
        return max(0, int(val))
    u_info = await asyncio.to_thread(db.get_user, uid) or {}
    raw_user_delay = u_info.get("interval_seconds")
    if raw_user_delay is not None:
        return max(0, int(raw_user_delay))
    return 15


async def _get_effective_loop(uid: int, s_name: str) -> int:
    val = await asyncio.to_thread(db.get_account_loop, s_name)
    if val is not None:
        return max(0, int(val))
    u_info = await asyncio.to_thread(db.get_user, uid) or {}
    raw_user_loop = u_info.get("loop_interval_seconds")
    if raw_user_loop is not None:
        return max(0, int(raw_user_loop))
    return 240


def _is_unauthorized_session_error(err: Exception) -> bool:
    msg = str(err or "").lower()
    return any(
        token in msg
        for token in (
            "oturum yetkisiz",
            "key is not registered",
            "deleted/deactivated",
            "user_deactivated",
            "auth_key_unregistered",
        )
    )


async def sender_loop(uid, context, chat_id, target_s_name=None):
    task_key = f"{uid}_auto_all_accounts" if not target_s_name else f"{uid}_acc_{target_s_name}"
    active_clients = {}
    tour_index = 0

    try:
        while True:
            if int(uid) in _explicitly_stopped_users:
                break
            if tasks.get(task_key) is not asyncio.current_task():
                break

            valid = await asyncio.to_thread(has_valid_access, uid)
            if not valid:
                await _notify_issue(
                    context,
                    chat_id,
                    "⚠️ ŞU SORUN ÇIKTI (Lisans Kontrolü):\nHata: LicenseExpiredOrMissing\nDetay: Aktif lisans bulunamadi\nDurum: 15 saniye sonra tekrar kontrol edilecek, sistem kapanmiyor.",
                )
                await _sleep_exact_seconds(15, uid, task_key, target_s_name)
                continue

            user_data = await asyncio.to_thread(db.get_user, uid)
            if (user_data or {}).get("is_running", 0) == 0:
                break
            if task_key not in tasks:
                break

            try:
                db_sessions = await asyncio.to_thread(db.get_user_sessions_db, uid)
                if target_s_name:
                    active_sessions = [
                        (s[0], s[1], s[2])
                        for s in (db_sessions or [])
                        if s[0] == target_s_name and s[2] and len(s[2].strip()) > 20
                    ]
                else:
                    active_sessions = [
                        (s[0], s[1], s[2])
                        for s in (db_sessions or [])
                        if s[2] and len(s[2].strip()) > 20
                    ]

                if not active_sessions:
                    await _sleep_exact_seconds(15, uid, task_key, target_s_name)
                    continue

                tour_index += 1
                log.info(f"[{task_key}] Tur #{tour_index} başladı | hesap sayısı: {len(active_sessions)}")

                for s_name, phone, s_string in active_sessions:
                    if int(uid) in _explicitly_stopped_users:
                        raise asyncio.CancelledError()
                    if task_key not in tasks or tasks.get(task_key) is not asyncio.current_task():
                        raise asyncio.CancelledError()
                    u_chk = await asyncio.to_thread(db.get_user, uid)
                    if (u_chk or {}).get("is_running", 0) == 0:
                        raise asyncio.CancelledError()

                    client = active_clients.get(s_name)
                    try:
                        client = await _ensure_client_connected(client, s_string, phone)
                        active_clients[s_name] = client
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        log.error(f"Client bağlantı hatası ({phone}): {e}")
                        await _safe_disconnect(client)
                        active_clients.pop(s_name, None)
                        clients.pop(s_name, None)
                        if _is_unauthorized_session_error(e):
                            await asyncio.to_thread(_set_single_account_state, s_name, False)
                            try:
                                await asyncio.to_thread(db.delete_session_db, s_name)
                            except Exception:
                                pass
                            await _notify_error(
                                context,
                                chat_id,
                                f"Oturum - {phone}",
                                e,
                                "Bu oturum devre disi birakildi, diger hesaplarla reklama devam ediliyor.",
                            )
                        else:
                            await _notify_error(
                                context,
                                chat_id,
                                f"Baglanti - {phone}",
                                e,
                                "Reklama kaldigi yerden devam ediliyor.",
                            )
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
                                ad_text = getattr(msg, "text", "") or getattr(msg, "message", "")
                                ad_entities = getattr(msg, "entities", None)
                        except asyncio.CancelledError:
                            raise
                        except Exception as e:
                            await _notify_error(
                                context,
                                chat_id,
                                f"Kayitli Mesaj - {phone}",
                                e,
                                "Reklama kaldigi yerden devam ediliyor.",
                            )

                    if not ad_text:
                        await _notify_issue(
                            context,
                            chat_id,
                            f"⚠️ ŞU SORUN ÇIKTI (Reklam Metni - {phone}):\nHata: MissingAdText\nDetay: Ozel metin veya kayitli mesaj bulunamadi\nDurum: Sonraki turda tekrar denenecek, reklama devam ediliyor.",
                        )
                        continue

                    try:
                        groups = []
                        async for dialog in client.iter_dialogs():
                            if int(uid) in _explicitly_stopped_users:
                                raise asyncio.CancelledError()
                            if task_key not in tasks or tasks.get(task_key) is not asyncio.current_task():
                                raise asyncio.CancelledError()
                            if dialog.is_group or (dialog.is_channel and getattr(dialog.entity, "megagroup", False)):
                                groups.append(dialog)
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        await _safe_disconnect(client)
                        active_clients.pop(s_name, None)
                        await _notify_error(
                            context,
                            chat_id,
                            f"Grup Listesi - {phone}",
                            e,
                            "Baglanti yenilenip reklama devam ediliyor.",
                        )
                        continue

                    if not groups:
                        continue

                    user_bl = blacklisted_groups.get(uid, set())
                    acc_bl_raw = await asyncio.to_thread(db.get_account_blacklist_db, s_name)
                    acc_bl_ids = set(acc_bl_raw or [])
                    acc_restricted = restricted_groups_by_account.get(s_name, {})
                    success_count, fail_count, skipped_count = 0, 0, 0
                    failed_group_list = []
                    delay_sec = await _get_effective_delay(uid, s_name)

                    for idx, group in enumerate(groups):
                        if int(uid) in _explicitly_stopped_users:
                            raise asyncio.CancelledError()
                        if task_key not in tasks or tasks.get(task_key) is not asyncio.current_task():
                            raise asyncio.CancelledError()
                        u_chk2 = await asyncio.to_thread(db.get_user, uid)
                        if (u_chk2 or {}).get("is_running", 0) == 0:
                            raise asyncio.CancelledError()

                        g_id, g_title = str(group.id), getattr(group, "title", "Grup")
                        entity = getattr(group, "entity", None)
                        g_username = f"@{entity.username}".lower() if entity and getattr(entity, "username", None) else ""

                        did_send = False
                        if (
                            g_id in user_bl
                            or (g_username and g_username in user_bl)
                            or g_id in acc_bl_ids
                            or g_id in acc_restricted
                        ):
                            skipped_count += 1
                        else:
                            while True:
                                try:
                                    client = await _send_single_message(
                                        client, s_string, phone, group.id, ad_text, ad_entities
                                    )
                                    active_clients[s_name] = client
                                    success_count += 1
                                    did_send = True
                                    break
                                except asyncio.CancelledError:
                                    raise
                                except FloodWaitError as exc:
                                    wait_sec = int(getattr(exc, "seconds", 30)) + 2
                                    await _notify_error(
                                        context,
                                        chat_id,
                                        f"FloodWait - {phone} ({g_title})",
                                        exc,
                                        f"{wait_sec} saniye bekleniyor, bitince ayni gruptan reklama devam edecek.",
                                    )
                                    await _sleep_exact_seconds(wait_sec, uid, task_key, target_s_name)
                                except Exception as exc:
                                    if _is_write_restricted_error(exc):
                                        _add_restricted_group(s_name, g_id, g_title, str(exc))
                                        fail_count += 1
                                        failed_group_list.append(g_title)
                                        await _notify_error(
                                            context,
                                            chat_id,
                                            f"Yazma Kisiti / Mute - {phone} ({g_title})",
                                            exc,
                                            "Grup 'Yazilamayan / Muteli Gruplar' listesine alindi.",
                                        )
                                        break
                                    if _is_connection_drop_error(exc):
                                        await _notify_error(
                                            context,
                                            chat_id,
                                            f"Baglanti Kopmasi - {phone} ({g_title})",
                                            exc,
                                            "Yeniden baglanip kaldigi yerden reklama devam ediliyor.",
                                        )
                                        await _safe_disconnect(client)
                                        active_clients.pop(s_name, None)
                                        await asyncio.sleep(2.0)
                                        continue
                                    fail_count += 1
                                    failed_group_list.append(g_title)
                                    await _notify_error(
                                        context,
                                        chat_id,
                                        f"Gonderim - {phone} ({g_title})",
                                        exc,
                                        "Sonraki gruba gecilip reklama devam ediliyor.",
                                    )
                                    break

                        if did_send and delay_sec > 0 and idx < len(groups) - 1:
                            await _sleep_exact_seconds(delay_sec, uid, task_key, target_s_name)

                    failed_reports_cache[s_name] = {
                        "phone": phone,
                        "success": success_count,
                        "fail": fail_count,
                        "skipped": skipped_count,
                        "groups": failed_group_list[-30:],
                    }

                loop_sec = await _get_effective_loop(uid, target_s_name or "default")
                if loop_sec > 0:
                    await _sleep_exact_seconds(loop_sec, uid, task_key, target_s_name)
            except asyncio.CancelledError:
                raise
            except Exception as loop_err:
                log.error(f"[{task_key}] Döngü hatası: {loop_err}")
                await _notify_error(context, chat_id, "Reklam Döngüsü", loop_err, "10 saniye sonra tekrar denenecek.")
                await _sleep_exact_seconds(10, uid, task_key, target_s_name)
    except asyncio.CancelledError:
        pass
    finally:
        for c in list(active_clients.values()):
            await _safe_disconnect(c)
        active_clients.clear()
        if task_key in tasks and tasks.get(task_key) is asyncio.current_task():
            tasks.pop(task_key, None)
async def show_pricing_panel(update, context):
    q = update.callback_query
    text = (
        "💎 **FARKETMEZDA PLATINUM | ABONELİK & FİYATLAR**\n\n"
        "• **Günlük Paket:** 200 TL\n"
        "• **Haftalık Paket:** 400 TL\n"
        "• **Aylık Paket:** 1000 TL\n\n"
        "Lisans almak için **`@farketmezda`** ile iletişime geçebilir veya elinizdeki anahtarı **🔑 KEY Aktif Et** menüsünden girebilirsiniz."
    )
    await q.edit_message_text(text, parse_mode="Markdown", reply_markup=sub_menu_keyboard())


async def show_accounts(update, context):
    q = update.callback_query
    uid = update.effective_user.id
    rows = await asyncio.to_thread(db.get_user_sessions_db, uid) or []
    if not rows:
        await q.edit_message_text(
            "👤 **Hesaplarım**\n\nHenüz kayıtlı hesabınız bulunmuyor. **➕ Hesap Ekle** butonundan ekleyebilirsiniz.",
            parse_mode="Markdown",
            reply_markup=sub_menu_keyboard(),
        )
        return

    lines = ["👤 **KAYITLI HESAPLARIM**\n"]
    kb_rows = []
    for idx, row in enumerate(rows, start=1):
        s_name = row[0]
        phone = row[1]
        uname = row[3] if len(row) > 3 and row[3] else ""
        u_tag = f" (@{uname})" if uname else ""
        lines.append(f"{idx}. `{phone}`{u_tag} — `{s_name}`")
        kb_rows.append([InlineKeyboardButton(f"🗑 Sil: {phone}", callback_data=f"del_acc:{s_name}")])
    kb_rows.append([InlineKeyboardButton("⬅ Geri / Ana Menü", callback_data="home")])
    await q.edit_message_text("\n".join(lines), parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb_rows))


async def show_start_stop_menu(update, context):
    q = update.callback_query
    uid = update.effective_user.id
    u_info = await asyncio.to_thread(db.get_user, uid) or {}
    is_run = int(u_info.get("is_running", 0) or 0) == 1
    rows = await asyncio.to_thread(db.get_user_sessions_db, uid) or []

    status_txt = "🟢 ÇALIŞIYOR" if is_run else "🔴 DURDURULDU"
    lines = [
        "▶️ **BAŞLAT / ⏹ DURDUR YÖNETİMİ**\n",
        f"• **Genel Reklam Durumu:** {status_txt}",
        f"• **Kayıtlı Hesap Sayısı:** `{len(rows)}`\n",
    ]
    kb_rows = [
        [
            InlineKeyboardButton("▶️ Tüm Hesapları Başlat", callback_data="start_all"),
            InlineKeyboardButton("⏹ Tümünü Durdur", callback_data="stop_all"),
        ]
    ]
    for row in rows[:15]:
        s_name, phone = row[0], row[1]
        kb_rows.append([
            InlineKeyboardButton(f"▶️ {phone}", callback_data=f"start_acc:{s_name}"),
            InlineKeyboardButton(f"⏹ {phone}", callback_data=f"stop_acc:{s_name}"),
        ])
    kb_rows.append([InlineKeyboardButton("⬅ Geri / Ana Menü", callback_data="home")])
    await q.edit_message_text("\n".join(lines), parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb_rows))


async def show_account_config_menu(update, context):
    q = update.callback_query
    uid = update.effective_user.id
    rows = await asyncio.to_thread(db.get_user_sessions_db, uid) or []
    u_info = await asyncio.to_thread(db.get_user, uid) or {}
    def_delay = u_info.get("interval_seconds", 15)
    def_loop = u_info.get("loop_interval_seconds", 240)

    lines = [
        "📩 **HESABA ÖZEL METİN & SÜRELER**\n",
        f"• **Varsayılan Mesaj Arası Süre:** `{def_delay}` sn",
        f"• **Varsayılan Tur Arası Süre:** `{def_loop}` sn\n",
        "Ayarlamak istediğiniz hesabı seçin:",
    ]
    kb_rows = []
    for row in rows[:15]:
        s_name, phone = row[0], row[1]
        c_txt = await asyncio.to_thread(db.get_account_custom_text, s_name)
        c_del = await asyncio.to_thread(db.get_account_delay, s_name)
        c_loop = await asyncio.to_thread(db.get_account_loop, s_name)
        badge = "📝" if c_txt else "📄"
        d_txt = f"{c_del}s" if c_del is not None else f"{def_delay}s"
        l_txt = f"{c_loop}s" if c_loop is not None else f"{def_loop}s"
        lines.append(f"{badge} `{phone}` → Ara: `{d_txt}` | Tur: `{l_txt}`")
        kb_rows.append([
            InlineKeyboardButton(f"📝 Metin: {phone}", callback_data=f"set_acc_text:{s_name}"),
            InlineKeyboardButton(f"⏱ Süre: {phone}", callback_data=f"set_acc_delay:{s_name}"),
        ])
    kb_rows.append([InlineKeyboardButton("⬅ Geri / Ana Menü", callback_data="home")])
    await q.edit_message_text("\n".join(lines), parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb_rows))


async def show_restricted_groups_menu(update, context):
    q = update.callback_query
    uid = update.effective_user.id
    rows = await asyncio.to_thread(db.get_user_sessions_db, uid) or []
    lines = ["🚧 **YAZILAMAYAN / MUTELİ GRUPLAR**\n"]
    total = 0
    for row in rows:
        s_name, phone = row[0], row[1]
        acc_map = restricted_groups_by_account.get(s_name, {})
        if acc_map:
            lines.append(f"📱 `{phone}` ({len(acc_map)} grup):")
            for g_id, info in list(acc_map.items())[:10]:
                total += 1
                lines.append(f"  • {info.get('title', g_id)} (`{g_id}`)")
    if total == 0:
        lines.append("Şu an yazılamayan veya muteli grup kaydı bulunmuyor.")
    lines.append("\nKaldırmak için: `/unmute_group <grup_id_veya_adi>`")
    await q.edit_message_text("\n".join(lines), parse_mode="Markdown", reply_markup=sub_menu_keyboard())


async def show_failed_reports(update, context):
    q = update.callback_query
    uid = update.effective_user.id
    rows = await asyncio.to_thread(db.get_user_sessions_db, uid) or []
    lines = ["📊 **BAŞARISIZ RAPORLAR**\n"]
    any_rep = False
    for row in rows:
        s_name, phone = row[0], row[1]
        rep = failed_reports_cache.get(s_name)
        if rep:
            any_rep = True
            lines.append(
                f"📱 `{phone}` — Başarılı: `{rep.get('success', 0)}` | "
                f"Başarısız: `{rep.get('fail', 0)}` | Atlanan: `{rep.get('skipped', 0)}`"
            )
            if rep.get("groups"):
                lines.append("  Hatalı gruplar: " + ", ".join(rep["groups"][-10:]))
    if not any_rep:
        lines.append("Henüz rapor verisi oluşmadı.")
    await q.edit_message_text("\n".join(lines), parse_mode="Markdown", reply_markup=sub_menu_keyboard())


async def show_blacklist_menu(update, context):
    q = update.callback_query
    uid = update.effective_user.id
    bl = sorted(list(blacklisted_groups.get(uid, set())))
    pending[uid] = {"step": "WAIT_BLACKLIST_ADD"}
    txt = (
        "🚫 **GENEL GRUP ENGEL LİSTESİ**\n\n"
        f"Kayıtlı engel sayısı: `{len(bl)}`\n"
        + (("\n• " + "\n• ".join(f"`{x}`" for x in bl[:20])) if bl else "")
        + "\n\nEngellenecek grup ID veya `@kullaniciadi` yazıp gönderin:"
    )
    await q.edit_message_text(txt, parse_mode="Markdown", reply_markup=sub_menu_keyboard())


async def show_redeem_menu(update, context):
    q = update.callback_query
    uid = update.effective_user.id
    pending[uid] = {"step": "WAIT_REDEEM_KEY"}
    await q.edit_message_text(
        "🔑 **LİSANS KEY AKTİF ET**\n\nLütfen elinizdeki lisans anahtarını (KEY) yazıp gönderin:",
        parse_mode="Markdown",
        reply_markup=sub_menu_keyboard(),
    )


async def show_admin_panel(update, context):
    q = update.callback_query
    uid = update.effective_user.id
    if not is_admin(uid):
        await q.edit_message_text("⛔ Bu alan yalnızca yöneticiye açıktır.", reply_markup=sub_menu_keyboard())
        return
    await q.edit_message_text(
        "👑 **YÖNETİCİ PANELİ**\n\nLisans anahtarı üretebilir, listeyi inceleyebilir veya duyuru geçebilirsiniz.",
        parse_mode="Markdown",
        reply_markup=admin_keyboard(),
    )


async def handle_callback(update, context):
    q = update.callback_query
    uid = update.effective_user.id
    data = q.data or ""
    try:
        await q.answer()
    except Exception:
        pass

    if data == "home":
        pending.pop(uid, None)
        await show_home(update, context)
        return
    if data == "guide_menu":
        await show_guide(update, context)
        return
    if data == "pricing_panel":
        await show_pricing_panel(update, context)
        return
    if data == "accounts":
        await show_accounts(update, context)
        return
    if data.startswith("del_acc:"):
        s_name = data.split(":", 1)[1]
        await asyncio.to_thread(db.delete_session_db, s_name)
        await show_accounts(update, context)
        return
    if data == "start_stop_menu":
        await show_start_stop_menu(update, context)
        return
    if data == "start_all":
        await start_sender(uid, q.message.chat_id, context, target_s_name=None)
        await show_start_stop_menu(update, context)
        return
    if data == "stop_all":
        _explicitly_stopped_users.add(int(uid))
        await asyncio.to_thread(db.set_user, uid, is_running=0)
        await _purge_user_stale_runs(uid, target_s_name=None)
        await show_start_stop_menu(update, context)
        return
    if data.startswith("start_acc:"):
        s_name = data.split(":", 1)[1]
        await start_sender(uid, q.message.chat_id, context, target_s_name=s_name)
        await show_start_stop_menu(update, context)
        return
    if data.startswith("stop_acc:"):
        s_name = data.split(":", 1)[1]
        await asyncio.to_thread(_set_single_account_state, s_name, False)
        await _cancel_task_immediately(f"{uid}_acc_{s_name}")
        await show_start_stop_menu(update, context)
        return
    if data == "account_config_menu":
        await show_account_config_menu(update, context)
        return
    if data.startswith("set_acc_text:"):
        s_name = data.split(":", 1)[1]
        pending[uid] = {"step": "WAIT_ACC_TEXT", "s_name": s_name}
        await q.edit_message_text(
            f"📝 `{s_name}` hesabı için yeni reklam metnini yazıp gönderin:",
            parse_mode="Markdown",
            reply_markup=sub_menu_keyboard(),
        )
        return
    if data.startswith("set_acc_delay:"):
        s_name = data.split(":", 1)[1]
        pending[uid] = {"step": "WAIT_ACC_DELAY", "s_name": s_name}
        await q.edit_message_text(
            f"⏱ `{s_name}` hesabı için `mesaj_arasi_sn tur_arasi_sn` şeklinde iki sayı girin (örnek: `15 240`):",
            parse_mode="Markdown",
            reply_markup=sub_menu_keyboard(),
        )
        return
    if data == "restricted_groups_menu":
        await show_restricted_groups_menu(update, context)
        return
    if data == "failed_reports":
        await show_failed_reports(update, context)
        return
    if data == "blacklist":
        await show_blacklist_menu(update, context)
        return
    if data == "redeem":
        await show_redeem_menu(update, context)
        return
    if data == "admin":
        await show_admin_panel(update, context)
        return
    if data == "key_create" and is_admin(uid):
        pending[uid] = {"step": "WAIT_ADMIN_KEY_SPEC"}
        await q.edit_message_text(
            "➕ İsim ve gün sayısını boşlukla yazın (örnek: `Ahmet 30`):",
            parse_mode="Markdown",
            reply_markup=admin_keyboard(),
        )
        return
    if data == "key_list" and is_admin(uid):
        keys = await asyncio.to_thread(db.list_keys_db) if hasattr(db, "list_keys_db") else []
        txt = "📋 **KEY LİSTESİ**\n\n" + ("\n".join(str(k) for k in (keys or [])[:25]) or "Kayıt bulunamadı.")
        await q.edit_message_text(txt, parse_mode="Markdown", reply_markup=admin_keyboard())
        return
    if data == "admin_broadcast" and is_admin(uid):
        pending[uid] = {"step": "WAIT_BROADCAST"}
        await q.edit_message_text(
            "📢 Tüm kullanıcılara gönderilecek duyuru metnini yazın:",
            parse_mode="Markdown",
            reply_markup=admin_keyboard(),
        )
        return
    if data == "add":
        pending[uid] = {"step": "WAIT_PHONE"}
        await q.edit_message_text(
            "📲 Lütfen başında ülke koduyla telefon numarasını girin (örnek: `+905551234567`):",
            parse_mode="Markdown",
            reply_markup=sub_menu_keyboard(),
        )
        return

    await show_home(update, context)


async def handle_message(update, context):
    uid = update.effective_user.id
    txt = (update.effective_message.text or "").strip()
    state = pending.get(uid)
    if not state:
        return

    step = state.get("step")
    if step == "WAIT_PHONE":
        s_name = f"acc_{uid}_{secrets.token_hex(3)}"
        await update.effective_message.reply_text("⏳ Kod isteği gönderiliyor, lütfen bekleyin...")
        ok, temp_client, clean_phone, p_hash, info_msg = await request_login_code_with_diag(txt)
        if not ok:
            pending.pop(uid, None)
            await update.effective_message.reply_text(f"❌ {info_msg}", reply_markup=sub_menu_keyboard())
            return
        pending[uid] = {
            "step": "WAIT_CODE",
            "s_name": s_name,
            "phone": clean_phone,
            "client": temp_client,
            "phone_code_hash": p_hash,
        }
        await update.effective_message.reply_text(info_msg, parse_mode="Markdown", reply_markup=sub_menu_keyboard())
        return

    if step == "WAIT_CODE":
        ok, next_state, res_msg = await confirm_login_code_and_save(
            uid=uid,
            s_name=state["s_name"],
            phone=state["phone"],
            temp_client=state["client"],
            phone_code_hash=state.get("phone_code_hash"),
            code=txt,
        )
        if not ok and next_state == "NEED_2FA":
            state["step"] = "WAIT_2FA"
            await update.effective_message.reply_text(res_msg, reply_markup=sub_menu_keyboard())
            return
        pending.pop(uid, None)
        await update.effective_message.reply_text(res_msg, reply_markup=get_keyboard(uid))
        return

    if step == "WAIT_2FA":
        ok, _, res_msg = await confirm_login_code_and_save(
            uid=uid,
            s_name=state["s_name"],
            phone=state["phone"],
            temp_client=state["client"],
            phone_code_hash=state.get("phone_code_hash"),
            code="",
            password=txt,
        )
        pending.pop(uid, None)
        await update.effective_message.reply_text(res_msg, reply_markup=get_keyboard(uid))
        return

    if step == "WAIT_ACC_TEXT":
        s_name = state["s_name"]
        await asyncio.to_thread(db.set_account_custom_text, s_name, txt)
        pending.pop(uid, None)
        await update.effective_message.reply_text("✅ Hesaba özel reklam metni kaydedildi.", reply_markup=get_keyboard(uid))
        return

    if step == "WAIT_ACC_DELAY":
        s_name = state["s_name"]
        parts = txt.split()
        try:
            d_val = max(0, int(parts[0]))
            l_val = max(0, int(parts[1])) if len(parts) > 1 else 240
            await asyncio.to_thread(db.set_account_delay, s_name, d_val)
            await asyncio.to_thread(db.set_account_loop, s_name, l_val)
            pending.pop(uid, None)
            await update.effective_message.reply_text(
                f"✅ Süreler güncellendi: Mesaj arası `{d_val}` sn | Tur arası `{l_val}` sn.",
                parse_mode="Markdown",
                reply_markup=get_keyboard(uid),
            )
        except Exception:
            await update.effective_message.reply_text("⚠️ Lütfen iki sayı girin, örneğin: `15 240`", parse_mode="Markdown")
        return

    if step == "WAIT_BLACKLIST_ADD":
        s_set = blacklisted_groups.setdefault(uid, set())
        s_set.add(txt.lower())
        pending.pop(uid, None)
        await update.effective_message.reply_text(f"✅ `{txt}` genel engel listesine eklendi.", parse_mode="Markdown", reply_markup=get_keyboard(uid))
        return

    if step == "WAIT_REDEEM_KEY":
        pending.pop(uid, None)
        try:
            ok, msg = await asyncio.to_thread(db.redeem_key_db, uid, txt) if hasattr(db, "redeem_key_db") else (False, "KEY sistemi kontrol edilemedi.")
            await update.effective_message.reply_text(msg, reply_markup=get_keyboard(uid))
        except Exception as e:
            await update.effective_message.reply_text(f"❌ KEY aktif edilemedi: {e}", reply_markup=get_keyboard(uid))
        return

    if step == "WAIT_ADMIN_KEY_SPEC" and is_admin(uid):
        pending.pop(uid, None)
        parts = txt.split()
        label = parts[0] if parts else "VIP"
        days = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 30
        code_str = f"PLATINUM-{label.upper()}-{''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(6))}"
        if hasattr(db, "create_key_db"):
            try:
                await asyncio.to_thread(db.create_key_db, code_str, days, label)
            except Exception:
                pass
        await update.effective_message.reply_text(f"✅ Key üretildi ({days} gün):\n`{code_str}`", parse_mode="Markdown", reply_markup=admin_keyboard())
        return

    if step == "WAIT_BROADCAST" and is_admin(uid):
        pending.pop(uid, None)
        users = await asyncio.to_thread(db.get_all_users_db) if hasattr(db, "get_all_users_db") else []
        sent_c = 0
        for u_row in users or []:
            target_id = u_row[0] if isinstance(u_row, (tuple, list)) else u_row
            try:
                await context.bot.send_message(chat_id=target_id, text=f"📢 **DUYURU**\n\n{txt}", parse_mode="Markdown")
                sent_c += 1
            except Exception:
                pass
        await update.effective_message.reply_text(f"✅ Duyuru `{sent_c}` kullanıcıya gönderildi.", parse_mode="Markdown", reply_markup=admin_keyboard())
        return


def main():
    Thread(target=run_web, daemon=True).start()
    Thread(target=_keep_alive_worker, daemon=True).start()

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("unmute_group", unmute_group_command))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    log.info("FARKETMEZDA PLATINUM bot başlatılıyor...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
