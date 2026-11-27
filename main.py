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
                                        await _sleep_exact_seconds(3, uid, task_key, target_s_name)
                                        try:
                                            client = await _ensure_client_connected(None, s_string, phone)
                                            active_clients[s_name] = client
                                            client = await _send_single_message(
                                                client, s_string, phone, group.id, ad_text, ad_entities
                                            )
                                            active_clients[s_name] = client
                                            success_count += 1
                                            did_send = True
                                            break
                                        except asyncio.CancelledError:
                                            raise
                                        except FloodWaitError:
                                            continue
                                        except Exception as retry_exc:
                                            if _is_write_restricted_error(retry_exc):
                                                _add_restricted_group(s_name, g_id, g_title, str(retry_exc))
                                            fail_count += 1
                                            failed_group_list.append(g_title)
                                            await _notify_error(
                                                context,
                                                chat_id,
                                                f"Gonderim Hatasi - {phone} ({g_title})",
                                                retry_exc,
                                                "Siradaki grupla reklama devam ediliyor.",
                                            )
                                            break
                                    fail_count += 1
                                    failed_group_list.append(g_title)
                                    await _notify_error(
                                        context,
                                        chat_id,
                                        f"Mesaj Gonderimi - {phone} ({g_title})",
                                        exc,
                                        "Bu grup atlanip reklama devam ediliyor.",
                                    )
                                    break

                        group_interval = await _get_effective_delay(uid, s_name)
                        if did_send and group_interval > 0 and idx < len(groups) - 1:
                            await _sleep_exact_seconds(group_interval, uid, task_key, target_s_name)

                    failed_reports_cache[s_name] = failed_group_list
                    loop_interval = await _get_effective_loop(uid, s_name)

                    if int(uid) in _explicitly_stopped_users:
                        raise asyncio.CancelledError()
                    if task_key not in tasks or tasks.get(task_key) is not asyncio.current_task():
                        raise asyncio.CancelledError()
                    u_chk3 = await asyncio.to_thread(db.get_user, uid)
                    if (u_chk3 or {}).get("is_running", 0) == 0:
                        raise asyncio.CancelledError()

                    report_text = (
                        f"📊 **[{phone}] REKLAM TURU TAMAMLANDI**\n"
                        f"✅ Başarılı: {success_count} | ❌ Hatalı: {fail_count} | 🚫 Atlanan: {skipped_count}\n"
                        f"⏳ *Sonraki tur {loop_interval} saniye sonra başlayacak.*"
                    )
                    try:
                        await context.bot.send_message(chat_id=chat_id, text=report_text, parse_mode="Markdown")
                    except Exception:
                        pass

                    await _safe_disconnect(active_clients.pop(s_name, None))
                    if loop_interval > 0:
                        await _sleep_exact_seconds(loop_interval, uid, task_key, target_s_name)

            except asyncio.CancelledError:
                raise
            except Exception as tour_err:
                for _, c in list(active_clients.items()):
                    await _safe_disconnect(c)
                active_clients.clear()
                await _notify_error(
                    context,
                    chat_id,
                    "Tur Dongusu",
                    tour_err,
                    "10 saniye sonra reklama kaldigi yerden devam edilecek.",
                )
                await asyncio.sleep(10)

    except asyncio.CancelledError:
        pass
    except Exception as e:
        await _notify_error(
            context,
            chat_id,
            "Ana Dongu",
            e,
            "10 saniye sonra dongu yeniden baslatilacak.",
        )
        await asyncio.sleep(10)
        if int(uid) not in _explicitly_stopped_users and tasks.get(task_key) is asyncio.current_task():
            tasks[task_key] = asyncio.create_task(sender_loop(uid, context, chat_id, target_s_name))
    finally:
        for _, c in list(active_clients.items()):
            await _safe_disconnect(c)
        active_clients.clear()
        if tasks.get(task_key) is asyncio.current_task():
            tasks.pop(task_key, None)


async def stop_sender(uid, chat_id, context, target_s_name=None):
    _explicitly_stopped_users.add(int(uid))
    if not target_s_name:
        await asyncio.to_thread(db.set_user, uid, is_running=0)
        try:
            user_sessions = await asyncio.to_thread(db.get_user_sessions_db, uid)
            for s_name, _, _ in user_sessions or []:
                await asyncio.to_thread(_set_single_account_state, str(s_name), False)
        except Exception:
            pass
        keys_to_stop = [k for k in list(tasks.keys()) if k.startswith(f"{uid}_")]
    else:
        await asyncio.to_thread(_set_single_account_state, target_s_name, False)
        keys_to_stop = [f"{uid}_acc_{target_s_name}"]
        remaining_running = await asyncio.to_thread(_get_running_single_accounts_for_user, uid)
        if not remaining_running and f"{uid}_auto_all_accounts" not in tasks:
            await asyncio.to_thread(db.set_user, uid, is_running=0)

    for key in keys_to_stop:
        await _cancel_task_immediately(key)

    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text="⏹ **Reklam otomasyonu anında ve güvenli bir şekilde durduruldu.**",
            parse_mode="Markdown",
        )
    except Exception:
        pass


async def resume_active_users_background(application):
    log.info("Açılışta otomatik başlatma kapalı: yalnızca kullanıcı Başlat komutu/butonu verdiğinde çalışacak.")


async def post_init(application):
    asyncio.create_task(resume_active_users_background(application))


async def _restart_running_account_task_if_active(uid: int, s_name: str, chat_id: int, context):
    acc_key = f"{uid}_acc_{s_name}"
    all_key = f"{uid}_auto_all_accounts"
    if acc_key in tasks and not tasks[acc_key].done():
        await start_sender(uid, chat_id, context, target_s_name=s_name, force_restart=True)
    elif all_key in tasks and not tasks[all_key].done():
        await start_sender(uid, chat_id, context, target_s_name=None, force_restart=True)


async def _render_restricted_groups_menu(q, uid: int):
    db_sessions = await asyncio.to_thread(db.get_user_sessions_db, uid) or []
    rows = []
    lines = ["🚧 **YAZILAMAYAN / MUTELİ GRUPLAR LİSTESİ**\n"]
    total_found = 0

    for s_name, phone, _ in db_sessions:
        acc_map = restricted_groups_by_account.get(s_name, {})
        if not acc_map:
            continue
        lines.append(f"📱 `{phone}`:")
        for g_id, info in list(acc_map.items())[:15]:
            total_found += 1
            g_title = info.get("title", g_id)
            lines.append(f"  • {g_title} (`{g_id}`)")
            rows.append([
                InlineKeyboardButton(
                    f"✅ Muteyi Aç / Tekrar Dene: {g_title[:24]}",
                    callback_data=f"unmute_grp_{s_name}_{g_id}",
                )
            ])

    if total_found == 0:
        lines.append("Şu an yazılamayan/muteli listesinde bekleyen grup yok.")
    else:
        lines.append("\nKurucu muteyi kaldırdıktan sonra ilgili butona basarak grubu tekrar reklam listesine alabilirsin.")

    rows.append([InlineKeyboardButton("⬅ Ana Menü", callback_data="home")])
    await q.edit_message_text("\n".join(lines), parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(rows))


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
    elif action == "restricted_groups_menu":
        await _render_restricted_groups_menu(q, uid)
    elif action.startswith("unmute_grp_"):
        payload = action.replace("unmute_grp_", "", 1)
        if "_" in payload:
            s_name, g_id = payload.rsplit("_", 1)
            ok = _remove_restricted_group(s_name, g_id)
            if ok:
                try:
                    await q.answer("✅ Grup listeden çıkarıldı, tekrar reklam atılacak!", show_alert=False)
                except Exception:
                    pass
        await _render_restricted_groups_menu(q, uid)
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
            [InlineKeyboardButton("⬅ Ana Menü", callback_data="home")],
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
            [InlineKeyboardButton("⬅ Geri", callback_data="pricing_panel")],
        ])
        await q.edit_message_text(pay_text, parse_mode="Markdown", reply_markup=kb)
    elif action == "send_receipt_prompt":
        context.user_data["flow"] = "awaiting_receipt"
        await q.edit_message_text(
            "📤 Lütfen yaptığınız ödemenin **dekont fotoğrafını** bota doğrudan gönderin:",
            parse_mode="Markdown",
            reply_markup=sub_menu_keyboard(),
        )
    elif action == "add":
        if not await asyncio.to_thread(has_valid_access, uid):
            await q.edit_message_text("❌ Aktif lisansınız yok.", reply_markup=sub_menu_keyboard())
            return
        context.user_data["flow"] = "phone"
        await q.edit_message_text(
            "➕ Telefon numaranızı +ülke kodu ile gönderin (`+90...`):",
            parse_mode="Markdown",
            reply_markup=sub_menu_keyboard(),
        )
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
        await q.edit_message_text(
            "👤 **HESAPLARIM VE KULLANICI ADLARI**",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(rows),
        )
    elif action.startswith("manage_"):
        s_name = action.replace("manage_", "")
        cur_delay = await _get_effective_delay(uid, s_name)
        cur_loop = await _get_effective_loop(uid, s_name)
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("▶ Başlat", callback_data=f"start_one_{s_name}"),
                InlineKeyboardButton("⏹ Durdur", callback_data=f"stop_one_{s_name}"),
            ],
            [InlineKeyboardButton("✍️ Bu Hesaba Özel Metin Gir", callback_data=f"set_acc_text_{s_name}")],
            [InlineKeyboardButton("⏱ Süreleri Ayarla", callback_data=f"set_acc_times_{s_name}")],
            [InlineKeyboardButton("🚧 Yazılamayan / Muteli Gruplar", callback_data="restricted_groups_menu")],
            [InlineKeyboardButton("🚫 Gruba Özel Engel", callback_data=f"acc_bl_{s_name}")],
            [InlineKeyboardButton("❌ Hesabı Sil", callback_data=f"del_{s_name}")],
            [InlineKeyboardButton("⬅ Geri", callback_data="accounts")],
        ])
        await q.edit_message_text(
            f"📱 **Hesap Yönetim Paneli**\n⏱ Grup Arası: `{cur_delay}s` | 🔄 Tur Dinlenme: `{cur_loop}s`",
            parse_mode="Markdown",
            reply_markup=kb,
        )
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
        await q.edit_message_text(
            "📩 **HESABA ÖZEL METİN VE SÜRE YAPILANDIRMASI**\n\nİşlem yapmak istediğiniz hesabı seçin:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(rows),
        )
    elif action.startswith("start_one_"):
        s_name = action.replace("start_one_", "")
        await start_sender(uid, q.message.chat_id, context, target_s_name=s_name, force_restart=True)
        await q.message.reply_text("▶ **Seçilen hesap için reklam başlatıldı!**", parse_mode="Markdown")
    elif action.startswith("stop_one_"):
        s_name = action.replace("stop_one_", "")
        await stop_sender(uid, q.message.chat_id, context, target_s_name=s_name)
    elif action.startswith("set_acc_text_"):
        s_name = action.replace("set_acc_text_", "")
        context.user_data["flow"] = f"acc_text_input_{s_name}"
        await q.edit_message_text(
            "✍️ Bu hesabın göndereceği **özel reklam metnini** yazın.\n\n"
            "💡 _Kayıtlı Mesajlar kullanmak için `sil` yazın._",
            parse_mode="Markdown",
            reply_markup=sub_menu_keyboard(),
        )
    elif action.startswith("set_acc_times_"):
        s_name = action.replace("set_acc_times_", "")
        context.user_data["flow"] = f"acc_times_delay_{s_name}"
        await q.edit_message_text(
            "⏱ Bu hesap için **grup arası bekleme süresini (saniye)** yazın:",
            parse_mode="Markdown",
            reply_markup=sub_menu_keyboard(),
        )
    elif action == "start_stop_menu":
        db_sessions = await asyncio.to_thread(db.get_user_sessions_db, uid)
        if not db_sessions:
            await q.edit_message_text("❌ Yönetilecek hesabınız yok.", reply_markup=get_keyboard(uid))
            return
        rows = [
            [
                InlineKeyboardButton("▶ Tümünü Başlat", callback_data="start_all_acc"),
                InlineKeyboardButton("⏹ Tümünü Anında Durdur", callback_data="stop_all_acc"),
            ]
        ]
        for s_name, phone, _ in db_sessions:
            uname = await asyncio.to_thread(db.get_account_username, s_name)
            disp = f"@{uname}" if uname else phone
            rows.append([
                InlineKeyboardButton(f"▶ Başlat: {disp}", callback_data=f"start_one_{s_name}"),
                InlineKeyboardButton(f"⏹ Durdur: {disp}", callback_data=f"stop_one_{s_name}"),
            ])
        rows.append([InlineKeyboardButton("⬅ Ana Menü", callback_data="home")])
        await q.edit_message_text(
            "▶️ **ANA PANELDEN BAŞLAT / DURDUR KONTROLÜ**",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(rows),
        )
    elif action == "start_all_acc":
        await start_sender(uid, q.message.chat_id, context, target_s_name=None, force_restart=True)
        await q.message.reply_text("▶ **Tüm hesaplar için reklam otomasyonu başlatıldı!**", parse_mode="Markdown")
    elif action == "stop_all_acc":
        await stop_sender(uid, q.message.chat_id, context, target_s_name=None)
    elif action == "failed_reports":
        db_sessions = await asyncio.to_thread(db.get_user_sessions_db, uid)
        report_lines = ["📊 **BAŞARISIZ GÖNDERİM RAPORLARI**\n"]
        for s_name, phone, _ in db_sessions or []:
            fails = failed_reports_cache.get(s_name, [])
            muted_cnt = len(restricted_groups_by_account.get(s_name, {}))
            report_lines.append(f"📱 `{phone}` -> Başarısız Grup: {len(fails)} | Muteli/Yazılamayan: {muted_cnt}")
            if fails:
                report_lines.append(f"  • Hata Alınanlar: {', '.join(fails[:5])}")
        await q.edit_message_text("\n".join(report_lines), parse_mode="Markdown", reply_markup=sub_menu_keyboard())
    elif action.startswith("acc_bl_"):
        s_name = action.replace("acc_bl_", "")
        context.user_data["flow"] = f"acc_blacklist_add_{s_name}"
        await q.edit_message_text(
            "🚫 Bu hesabın mesaj atmayacağı grubun linkini veya kullanıcı adını gönderin:",
            reply_markup=sub_menu_keyboard(),
        )
    elif action.startswith("del_"):
        s_name = action.replace("del_", "")
        restricted_groups_by_account.pop(s_name, None)
        await asyncio.to_thread(_set_single_account_state, s_name, False)
        await _cancel_task_immediately(f"{uid}_acc_{s_name}")
        await asyncio.to_thread(db.delete_session_db, s_name)
        await _safe_disconnect(clients.pop(s_name, None))
        await q.edit_message_text("✅ Hesap silindi.", reply_markup=get_keyboard(uid))
    elif action == "blacklist":
        context.user_data["flow"] = "blacklist"
        await q.edit_message_text(
            "🚫 Genel engellenecek grup linkini veya kullanıcı adını gönderin:",
            reply_markup=sub_menu_keyboard(),
        )
    elif action == "redeem":
        context.user_data["flow"] = "redeem"
        await q.edit_message_text("🔑 KEY kodunu girin:", reply_markup=sub_menu_keyboard())
    elif action == "admin" and is_admin(uid):
        await q.edit_message_text(
            "👑 **YÖNETİCİ LİSANS & İSİMLİ KEY ÜRETİM PANELİ**",
            parse_mode="Markdown",
            reply_markup=admin_keyboard(),
        )
    elif action == "key_create" and is_admin(uid):
        context.user_data["flow"] = "key_note_and_days"
        await q.edit_message_text(
            "➕ **İsimli KEY Üretimi**\n\nFormat: `İsim,GünSayısı`",
            parse_mode="Markdown",
            reply_markup=admin_keyboard(),
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
    for r in rows or []:
        code = r["code"]
        note = r.get("note", "İsimsiz")
        status_icon = "🟢 Aktif" if not r["is_used"] else "🔴 Kullanıldı"
        kb_list.append([
            InlineKeyboardButton(f"{note} | {code} ({r['duration_days']}g) - {status_icon}", callback_data="ignore"),
            InlineKeyboardButton("🗑 İptal Et", callback_data=f"rev_key_{code}"),
        ])
    kb_list.append([InlineKeyboardButton("⬅ Ana Menü", callback_data="home")])
    await q.edit_message_text("📋 **KEY LİSTESİ VE SAHİPLERİ**", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb_list))


async def text_handler(update, context):
    uid = update.effective_user.id
    text = update.effective_message.text.strip() if update.effective_message.text else ""
    flow = context.user_data.get("flow")

    if update.effective_message.photo and flow == "awaiting_receipt":
        photo = update.effective_message.photo[-1]
        context.user_data.clear()
        await update.effective_message.reply_text("✅ Dekont yöneticiye iletildi.", reply_markup=get_keyboard(uid))
        kb_admin = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Onayla", callback_data=f"approve_pay_{uid}"),
                InlineKeyboardButton("❌ Reddet", callback_data=f"reject_pay_{uid}"),
            ]
        ])
        try:
            await context.bot.send_photo(
                chat_id=ADMIN_ID,
                photo=photo.file_id,
                caption=f"🚨 **ÖDEME TALEBİ**\nKullanıcı: `{uid}`",
                parse_mode="Markdown",
                reply_markup=kb_admin,
            )
        except Exception:
            pass
        return

    if not flow:
        return

    if flow.startswith("acc_text_input_"):
        s_name = flow.replace("acc_text_input_", "")
        if text.lower() == "sil":
            await asyncio.to_thread(db.set_account_custom_text, s_name, "")
            await update.effective_message.reply_text(
                "✅ Özel metin kaldırıldı (Kayıtlı Mesajlara dönüldü).",
                reply_markup=get_keyboard(uid),
            )
        else:
            await asyncio.to_thread(db.set_account_custom_text, s_name, clean_bold_stars(text))
            await update.effective_message.reply_text("✅ Özel reklam metni kaydedildi!", reply_markup=get_keyboard(uid))
        context.user_data.clear()
        return

    if flow.startswith("acc_times_delay_"):
        s_name = flow.replace("acc_times_delay_", "")
        try:
            val = int(text)
            if val < 0:
                await update.effective_message.reply_text("Lütfen 0 veya daha büyük bir sayı girin.")
                return
            await asyncio.to_thread(db.set_account_delay, s_name, val)
            context.user_data["flow"] = f"acc_times_loop_{s_name}"
            await update.effective_message.reply_text(
                f"✅ Grup arası bekleme süresi `{val}` saniye kaydedildi.\n\n"
                "⏱ Şimdi **Tur Dinlenme Süresini (saniye)** yazın:",
                parse_mode="Markdown",
                reply_markup=sub_menu_keyboard(),
            )
        except ValueError:
            await update.effective_message.reply_text("Lütfen geçerli bir sayı girin.")
        return

    if flow.startswith("acc_times_loop_"):
        s_name = flow.replace("acc_times_loop_", "")
        try:
            val = int(text)
            if val < 0:
                await update.effective_message.reply_text("Lütfen 0 veya daha büyük bir sayı girin.")
                return
            await asyncio.to_thread(db.set_account_loop, s_name, val)
            saved_delay = await _get_effective_delay(uid, s_name)
            context.user_data.clear()
            await _restart_running_account_task_if_active(uid, s_name, update.effective_message.chat_id, context)
            await update.effective_message.reply_text(
                f"✅ Süreler güncellendi!\n• Grup Arası: `{saved_delay}s`\n• Tur Dinlenme: `{val}s`",
                parse_mode="Markdown",
                reply_markup=get_keyboard(uid),
            )
        except ValueError:
            await update.effective_message.reply_text("Lütfen geçerli bir sayı girin.")
        return

    if flow == "key_note_and_days" and is_admin(uid):
        try:
            parts = text.split(",")
            if len(parts) < 2:
                await update.effective_message.reply_text("❌ Hatalı format! `İsim,GünSayısı` şeklinde yazın.", parse_mode="Markdown")
                return
            note = parts[0].strip()
            days = int(parts[1].strip())
            code = "FKD-" + "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(10))
            await asyncio.to_thread(db.create_license, code, days, note=note)
            context.user_data.clear()
            await update.effective_message.reply_text(
                f"✅ **KEY Üretildi:** `{code}` ({note} - {days} Gün)",
                parse_mode="Markdown",
                reply_markup=admin_keyboard(),
            )
        except ValueError:
            await update.effective_message.reply_text("❌ Gün sayısı sayı olmalıdır.")
        return

    if flow == "admin_broadcast_msg" and is_admin(uid):
        users = await asyncio.to_thread(db.get_all_users)
        for u_id in users or []:
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
            timeout=20,
        )
        await client.connect()
        try:
            sent = await client.send_code_request(text)
            pending[uid] = {
                "client": client,
                "phone": text,
                "hash": sent.phone_code_hash,
                "session_name": session_name,
            }
            context.user_data["flow"] = "code"
            await update.effective_message.reply_text(
                "📲 Kod **Telegram uygulamanızdaki resmi 'Telegram' sohbetine** gönderildi.\n"
                "Lütfen o sohbeti kontrol edip gelen kodu girin:",
                parse_mode="Markdown",
                reply_markup=sub_menu_keyboard(),
            )
        except Exception as e:
            await _safe_disconnect(client)
            await update.effective_message.reply_text(f"Hata: {e}")

    elif flow == "code":
        item = pending.get(uid)
        if not item:
            await update.effective_message.reply_text(
                "⚠️ Oturum isteği sıfırlanmış, lütfen numarayı tekrar girin.",
                reply_markup=get_keyboard(uid),
            )
            return
        code_clean = re.sub(r"\s+", "", text)
        try:
            await item["client"].sign_in(phone=item["phone"], code=code_clean, phone_code_hash=item["hash"])
            s_name = item["session_name"]
            raw_s_string = item["client"].session.save()
            await _safe_disconnect(item["client"])
            clients.pop(s_name, None)
            await _verify_and_save_session(uid, s_name, item["phone"], raw_s_string)
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
            raw_s_string = item["client"].session.save()
            await _safe_disconnect(item["client"])
            clients.pop(s_name, None)
            await _verify_and_save_session(uid, s_name, item["phone"], raw_s_string)
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
    app.add_handler(CommandHandler("unmute_group", unmute_group_command))
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    app.add_handler(MessageHandler(filters.PHOTO, text_handler))
    app.run_polling(drop_pending_updates=True, poll_interval=0.5, timeout=15)


if __name__ == "__main__":
    main()
