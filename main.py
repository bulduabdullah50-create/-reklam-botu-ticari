import os
import re
import asyncio
import logging
import secrets
import string
import datetime
from threading import Thread
from flask import Flask
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError, FloodWaitError
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters
import database as db

app_web = Flask(__name__)
@app_web.route('/')
def home_web(): return "💎 FARKETMEZDA PLATINUM REKLAM BOTU AKTİF!"
def run_web(): app_web.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("farketmezda")

BOT_TOKEN = os.environ["BOT_TOKEN"]
API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
ADMIN_ID = int(os.environ["ADMIN_ID"])

clients, pending, tasks, blacklisted_groups = {}, {}, {}, {}

def is_admin(uid): return int(uid) == ADMIN_ID
def has_valid_access(uid): return is_admin(uid) or db.license_valid(uid, ADMIN_ID)

def get_keyboard(uid):
    rows = [
        [InlineKeyboardButton("💎 ABONELİK & FİYATLAR", callback_data="pricing_panel")],
        [InlineKeyboardButton("➕ Hesap Ekle", callback_data="add"), InlineKeyboardButton("👤 Hesaplarım", callback_data="accounts")],
        [InlineKeyboardButton("📩 Reklam İçeriği Ayarla", callback_data="ad_menu"), InlineKeyboardButton("🚫 Genel Grup Engel", callback_data="blacklist")],
        [InlineKeyboardButton("⏱ Grup Aralığı", callback_data="time_group"), InlineKeyboardButton("⏳ Tur Dinlenme Süresi", callback_data="time_loop")],
        [InlineKeyboardButton("🔑 KEY Aktif Et", callback_data="redeem")],
        [InlineKeyboardButton("💬 Canlı Destek", url="https://t.me/farketmezda")]
    ]
    if is_admin(uid):
        rows.append([InlineKeyboardButton("👑 YÖNETİCİ REKLAM PANELİ", callback_data="admin")])
    return InlineKeyboardMarkup(rows)

def sub_menu_keyboard(): return InlineKeyboardMarkup([[InlineKeyboardButton("⬅ Geri / Ana Menü", callback_data="home")]])
def admin_keyboard(): return InlineKeyboardMarkup([
    [InlineKeyboardButton("➕ KEY Üret", callback_data="key_create"), InlineKeyboardButton("📋 KEY Listesi", callback_data="key_list")],
    [InlineKeyboardButton("📢 Duyuru Geç", callback_data="admin_broadcast")],
    [InlineKeyboardButton("⬅ Ana Menü", callback_data="home")]
])

async def show_home(update, context):
    uid = update.effective_user.id
    db.ensure_user(uid, update.effective_user.username)
    expiry = db.get_user_expiry(uid)
    
    expiry_str = "Süre Bulunmuyor ❌"
    if expiry:
        try:
            exp_dt = datetime.datetime.strptime(expiry, "%Y-%m-%d %H:%M:%S.%f" if "." in expiry else "%Y-%m-%d %H:%M:%S")
            remaining = exp_dt - datetime.datetime.now()
            if remaining.total_seconds() > 0:
                days = remaining.days
                hours = remaining.seconds // 3600
                expiry_str = f"🟢 Aktif ({days} Gün, {hours} Saat Kaldı)"
            else:
                expiry_str = "🔴 Süresi Dolmuş"
        except:
            expiry_str = str(expiry)[:16]

    text = (
        "✨ **FARKETMEZDA PLATINUM OTOMASYON** ✨\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 Kullanıcı ID: `{uid}`\n"
        f"🛡 Abonelik Durumu: {expiry_str}\n\n"
        "🚀 Sistem kesintisiz çalışır. Bakım veya çökmeler sonrasında bot otomatik olarak kaldığı yerden devam eder.\n"
        "🔒 *Kanallara asla mesaj atılmaz, sadece gruplara gönderim yapılır.*\n\n"
        "👇 İşlem yapmak için aşağıdaki butonları kullanın:"
    )
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(uid))
    else:
        await update.effective_message.reply_text(text, parse_mode="Markdown", reply_markup=get_keyboard(uid))

async def start(update, context):
    context.user_data.clear()
    await show_home(update, context)

# --- REKLAM GÖNDERİM MOTORU ---
async def start_sender(uid, chat_id, context):
    if not has_valid_access(uid):
        return
    
    task_key = f"{uid}_auto_all_accounts"
    if task_key in tasks and not tasks[task_key].done():
        return

    db.set_user(uid, is_running=1)
    tasks[task_key] = asyncio.create_task(sender_loop(uid, context, chat_id))

async def sender_loop(uid, context, chat_id):
    task_key = f"{uid}_auto_all_accounts"
    while True:
        try:
            if not has_valid_access(uid):
                db.set_user(uid, is_running=0)
                break
            
            user_data = db.get_user(uid)
            if not user_data or user_data.get("is_running", 0) == 0:
                break

            db_sessions = db.get_user_sessions_db(uid)
            active_sessions = [(s[0], s[1], s[2]) for s in db_sessions if s[2] and len(s[2].strip()) > 50]

            if not active_sessions:
                await asyncio.sleep(20)
                continue

            loop_interval = user_data.get("loop_interval_seconds", 240)
            group_interval = user_data.get("interval_seconds", 3)
            custom_text = user_data.get("custom_ad_text", "").strip()

            for s_name, phone, s_string in active_sessions:
                if task_key not in tasks or db.get_user(uid).get("is_running", 0) == 0: 
                    break
                
                client = None
                try:
                    clean_string = s_string.strip().strip('"').strip("'").replace("\n", "").strip()
                    client = TelegramClient(StringSession(clean_string), API_ID, API_HASH, connection_retries=3, auto_reconnect=True, timeout=30)
                    await client.connect()
                    
                    if not await client.is_user_authorized():
                        await client.disconnect()
                        continue
                except Exception:
                    continue

                ad_text = ""
                if custom_text:
                    ad_text = custom_text
                else:
                    try:
                        messages = await client.get_messages("me", limit=1)
                        if messages:
                            ad_text = getattr(messages[0], 'text', '') or getattr(messages[0], 'message', '')
                    except:
                        pass
                
                if not ad_text:
                    await client.disconnect()
                    continue

                try:
                    groups = []
                    async for dialog in client.iter_dialogs():
                        if dialog.is_group or (dialog.is_channel and getattr(dialog.entity, 'megagroup', False)):
                            groups.append(dialog)
                except Exception:
                    await client.disconnect()
                    continue

                if not groups:
                    await client.disconnect()
                    continue

                user_bl = blacklisted_groups.get(uid, set())
                acc_bl_ids = set(db.get_account_blacklist_db(s_name))
                success_count, fail_count, skipped_count = 0, 0, 0
                sent_details = []

                for group in groups:
                    if task_key not in tasks: break
                    g_id, g_title = str(group.id), getattr(group, 'title', 'Grup')
                    entity = getattr(group, 'entity', None)
                    g_username = f"@{entity.username}".lower() if entity and getattr(entity, 'username', None) else ""

                    if g_id in user_bl or (g_username and g_username in user_bl) or g_id in acc_bl_ids:
                        skipped_count += 1
                        continue
                    
                    try:
                        await client.send_message(group.id, ad_text)
                        success_count += 1
                        sent_details.append(g_title)
                    except FloodWaitError as exc:
                        await asyncio.sleep(exc.seconds + 5)
                        try:
                            await client.send_message(group.id, ad_text)
                            success_count += 1
                            sent_details.append(g_title)
                        except:
                            fail_count += 1
                    except Exception:
                        fail_count += 1
                    
                    if group_interval > 0: 
                        await asyncio.sleep(group_interval)

                report_text = (
                    f"📊 **[{phone}] REKLAM TURU TAMAMLANDI**\n"
                    f"✅ Başarılı: {success_count} | ❌ Hatalı: {fail_count} | 🚫 Atlanan: {skipped_count}"
                )
                try:
                    await context.bot.send_message(chat_id=chat_id, text=report_text, parse_mode="Markdown")
                except: pass

                try: await client.disconnect()
                except: pass

                await asyncio.sleep(3)

            await asyncio.sleep(loop_interval)

        except asyncio.CancelledError:
            break
        except Exception as e:
            log.exception("Döngü hatası: %s", e)
            await asyncio.sleep(15)
            
    tasks.pop(task_key, None)

async def stop_sender(uid, chat_id, context):
    db.set_user(uid, is_running=0)
    task_key = f"{uid}_auto_all_accounts"
    task = tasks.get(task_key)
    if task and not task.done():
        task.cancel()
        tasks.pop(task_key, None)
    await context.bot.send_message(chat_id=chat_id, text="⏹ **Reklam otomasyonu durduruldu.**")

# Bot açıldığında çalışan kullanıcıları otomatik başlatma arka plan görevi
async def resume_active_users_background(application):
    await asyncio.sleep(5)
    running_users = db.get_all_running_users()
    for uid in running_users:
        if db.license_valid(uid, ADMIN_ID):
            try:
                class DummyContext:
                    def __init__(self, bot):
                        self.bot = bot
                tasks[f"{uid}_auto_all_accounts"] = asyncio.create_task(sender_loop(uid, DummyContext(application.bot), uid))
                log.info(f"Kullanıcı {uid} otomatik olarak kaldığı yerden başlatıldı.")
            except Exception as e:
                log.error(f"Kullanıcı {uid} başlatılamadı: {e}")
        else:
            db.set_user(uid, is_running=0)

async def post_init(application):
    asyncio.create_task(resume_active_users_background(application))

# --- CALLBACK VE ARAYÜZ YÖNETİMİ ---
async def button(update, context):
    q = update.callback_query
    await q.answer()
    uid, action = q.from_user.id, q.data
    db.ensure_user(uid, q.from_user.username)

    if action == "home": 
        await q.edit_message_text("✨ FARKETMEZDA PLATINUM PANEL", reply_markup=get_keyboard(uid))
    
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
        p_name, p_price, days = ("Günlük Paket", "200 TL", 1) if action == "buy_daily" else (("Haftalık Paket", "400 TL", 7) if action == "buy_weekly" else ("Aylık Paket", "1000 TL", 30))
        context.user_data["pending_buy_days"] = days
        context.user_data["pending_buy_name"] = p_name

        pay_text = (
            f"💳 **ÖDEME BİLDİRİMİ VE DETAYLARI**\n"
            f"📦 Paket: **{p_name}** | Tutar: **{p_price}**\n\n"
            f"🏦 **İBAN:** `TR100006200091000006969709`\n"
            f"• **Alıcı:** Garanti Ödeme ve Elektronik Para Hizmetleri A.Ş.\n"
            f"• **Açıklama:** `TAMİ7696919784875077`\n\n"
            f"⚠️ Açıklamaya kodu yazıp ödemeyi yaptıktan sonra dekont gönderin."
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
        if not has_valid_access(uid):
            await q.edit_message_text("❌ Aktif lisansınız yok.", reply_markup=sub_menu_keyboard())
            return
        context.user_data["flow"] = "phone"
        await q.edit_message_text("➕ Telefon numaranızı +ülke kodu ile gönderin (`+90...`):", parse_mode="Markdown", reply_markup=sub_menu_keyboard())

    elif action == "accounts":
        db_sessions = db.get_user_sessions_db(uid)
        if not db_sessions:
            await q.edit_message_text("👤 Kayıtlı hesabınız yok.", reply_markup=get_keyboard(uid))
            return
        rows = [[InlineKeyboardButton(f"📱 {phone}", callback_data=f"manage_{s_name}")] for s_name, phone, _ in db_sessions]
        rows.append([InlineKeyboardButton("⬅ Ana Menü", callback_data="home")])
        await q.edit_message_text("👤 **HESAPLARIM**", reply_markup=InlineKeyboardMarkup(rows))

    elif action.startswith("manage_"):
        s_name = action.replace("manage_", "")
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("▶ Otomasyonu Başlat", callback_data="start_run"), InlineKeyboardButton("⏹ Otomasyonu Durdur", callback_data="stop_run")],
            [InlineKeyboardButton("🚫 Gruba Özel Engel", callback_data=f"acc_bl_{s_name}")],
            [InlineKeyboardButton("❌ Hesabı Sil", callback_data=f"del_{s_name}")],
            [InlineKeyboardButton("⬅ Geri", callback_data="accounts")]
        ])
        await q.edit_message_text("📱 **Hesap Yönetimi**", reply_markup=kb)

    elif action.startswith("acc_bl_"):
        s_name = action.replace("acc_bl_", "")
        context.user_data["flow"] = f"acc_blacklist_add_{s_name}"
        await q.edit_message_text("🚫 Bu hesabın mesaj atmayacağı grubun linkini veya kullanıcı adını gönderin:", reply_markup=sub_menu_keyboard())

    elif action == "start_run":
        if not has_valid_access(uid):
            await q.message.reply_text("❌ Aktif lisansınız yok!")
            return
        await start_sender(uid, q.message.chat_id, context)
        await q.message.reply_text("▶ **Otomasyon Başlatıldı!** (Kayıt altına alındı, kesintilerden etkilenmez)")

    elif action == "stop_run":
        await stop_sender(uid, q.message.chat_id, context)

    elif action.startswith("del_"):
        s_name = action.replace("del_", "")
        db.delete_session_db(s_name)
        clients.pop(s_name, None)
        await q.edit_message_text("✅ Hesap silindi.", reply_markup=get_keyboard(uid))

    elif action == "ad_menu":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📩 Kayıtlı Mesajlardan Al", callback_data="ad_saved")],
            [InlineKeyboardButton("✍️ Özel Metin Yaz", callback_data="ad_custom")],
            [InlineKeyboardButton("⬅ Ana Menü", callback_data="home")]
        ])
        await q.edit_message_text("📩 **REKLAM KAYNAĞI**", reply_markup=kb)

    elif action == "ad_saved":
        db.set_user(uid, custom_ad_text="")
        await q.edit_message_text("✅ Kayıtlı Mesajlar (Saved Messages) seçildi.", reply_markup=get_keyboard(uid))

    elif action == "ad_custom":
        context.user_data["flow"] = "set_custom_ad"
        await q.edit_message_text("✍️ Gönderilmesini istediğiniz reklam metnini yazın:", reply_markup=sub_menu_keyboard())

    elif action == "blacklist":
        context.user_data["flow"] = "blacklist"
        await q.edit_message_text("🚫 Genel engellenecek grup linkini veya kullanıcı adını gönderin:", reply_markup=sub_menu_keyboard())

    elif action == "time_group":
        context.user_data["flow"] = "interval_group"
        await q.edit_message_text("⏱ İki grup arası bekleme süresi (saniye):", reply_markup=sub_menu_keyboard())

    elif action == "time_loop":
        context.user_data["flow"] = "interval_loop"
        await q.edit_message_text("⏳ Tur dinlenme süresi (saniye):", reply_markup=sub_menu_keyboard())

    elif action == "redeem":
        context.user_data["flow"] = "redeem"
        await q.edit_message_text("🔑 KEY kodunu girin:", reply_markup=sub_menu_keyboard())

    elif action == "admin" and is_admin(uid):
        await q.edit_message_text("👑 **YÖNETİCİ PANELİ**", reply_markup=admin_keyboard())

    elif action == "key_create" and is_admin(uid):
        context.user_data["flow"] = "key_days"
        await q.edit_message_text("Kaç günlük KEY üretilsin?", reply_markup=admin_keyboard())

    elif action == "key_list" and is_admin(uid):
        rows = db.list_licenses()
        txt = "📋 **KEY LİSTESİ**\n\n" + ("\n".join(f"`{r['code']}` | {r['duration_days']} Gün | Kullanıldı: {'Evet ❌' if r['is_used'] else 'Hayır ✅'}" for r in rows) if rows else "Yok.")
        await q.edit_message_text(txt[:3900], parse_mode="Markdown", reply_markup=admin_keyboard())

    elif action == "admin_broadcast" and is_admin(uid):
        context.user_data["flow"] = "admin_broadcast_msg"
        await q.edit_message_text("📢 Tüm kullanıcılara gönderilecek duyuru mesajını yazın:", reply_markup=admin_keyboard())

    elif action.startswith("approve_pay_") and is_admin(uid):
        target_uid = int(action.replace("approve_pay_", ""))
        db.add_license_time_db(target_uid, 30)
        await q.edit_message_text(f"✅ Ödeme onaylandı, kullanıcıya süre eklendi.")
        try:
            await context.bot.send_message(chat_id=target_uid, text="🎉 Ödemeniz onaylandı! Süreniz hesabınıza tanımlandı.")
        except: pass

    elif action.startswith("reject_pay_") and is_admin(uid):
        target_uid = int(action.replace("reject_pay_", ""))
        await q.edit_message_text(f"❌ Ödeme reddedildi.")
        try:
            await context.bot.send_message(chat_id=target_uid, text="❌ Ödemeniz onaylanmadı.")
        except: pass

# --- METİN VE FOTOĞRAF İŞLEYİCİSİ ---
async def text_handler(update, context):
    uid = update.effective_user.id
    text = update.effective_message.text.strip() if update.effective_message.text else ""
    flow = context.user_data.get("flow")
    
    if update.effective_message.photo and flow == "awaiting_receipt":
        photo = update.effective_message.photo[-1]
        days = context.user_data.get("pending_buy_days", 30)
        p_name = context.user_data.get("pending_buy_name", "Paket")
        
        context.user_data.clear()
        await update.effective_message.reply_text("✅ Dekont yöneticiye iletildi, onay bekleniyor.", reply_markup=get_keyboard(uid))
        
        kb_admin = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Onayla", callback_data=f"approve_pay_{uid}"), InlineKeyboardButton("❌ Reddet", callback_data=f"reject_pay_{uid}")]
        ])
        try:
            await context.bot.send_photo(chat_id=ADMIN_ID, photo=photo.file_id, caption=f"🚨 **ÖDEME ONAY TALEBİ**\nKullanıcı: `{uid}`\nPaket: {p_name} ({days} Gün)", reply_markup=kb_admin)
        except: pass
        return

    if not flow: return

    if flow == "admin_broadcast_msg" and is_admin(uid):
        for u_id in db.get_all_users():
            try: await context.bot.send_message(chat_id=u_id, text=f"📢 **DUYURU**\n\n{text}", parse_mode="Markdown")
            except: pass
        context.user_data.clear()
        await update.effective_message.reply_text("✅ Duyuru iletildi.", reply_markup=admin_keyboard())
        return

    if flow == "phone":
        if not re.fullmatch(r"\+[1-9]\d{7,14}", text):
            await update.effective_message.reply_text("Geçerli numara girin (+90...)")
            return
        session_name = f"SESSION_{uid}_{text}"
        client = TelegramClient(StringSession(), API_ID, API_HASH, connection_retries=2, auto_reconnect=True)
        await client.connect()
        try:
            sent = await client.send_code_request(text)
            pending[uid] = {"client": client, "phone": text, "hash": sent.phone_code_hash, "session_name": session_name}
            context.user_data["flow"] = "code"
            await update.effective_message.reply_text("📲 Telegram kodunu girin:")
        except Exception as e:
            await client.disconnect()
            await update.effective_message.reply_text(f"Hata: {e}")

    elif flow == "code":
        item = pending.get(uid)
        if not item: return
        try:
            await item["client"].sign_in(phone=item["phone"], code=text, phone_code_hash=item["hash"])
            s_name = item["session_name"]
            s_string = item["client"].session.save()
            clients[s_name] = item["client"]
            db.save_session_db(uid, s_name, item["phone"], s_string)
            pending.pop(uid, None)
            context.user_data.clear()
            await update.effective_message.reply_text("✅ Hesap başarıyla bağlandı!", reply_markup=get_keyboard(uid))
        except SessionPasswordNeededError:
            context.user_data["flow"] = "2fa"
            await update.effective_message.reply_text("🔒 2FA şifrenizi girin:")
        except Exception as e:
            await update.effective_message.reply_text(f"Hata: {e}")

    elif flow == "2fa":
        item = pending.get(uid)
        if not item: return
        try:
            await item["client"].sign_in(password=text)
            s_name = item["session_name"]
            s_string = item["client"].session.save()
            clients[s_name] = item["client"]
            db.save_session_db(uid, s_name, item["phone"], s_string)
            pending.pop(uid, None)
            context.user_data.clear()
            await update.effective_message.reply_text("✅ Hesap bağlandı!", reply_markup=get_keyboard(uid))
        except Exception as e:
            await update.effective_message.reply_text(f"Hata: {e}")

    elif flow.startswith("acc_blacklist_add_"):
        s_name = flow.replace("acc_blacklist_add_", "")
        target = text.replace("https://t.me/", "").replace("t.me/", "").strip().lstrip("@").lower()
        db.add_account_blacklist_db(s_name, target)
        context.user_data.clear()
        await update.effective_message.reply_text(f"✅ Engellendi: {target}", reply_markup=get_keyboard(uid))

    elif flow == "set_custom_ad":
        db.set_user(uid, custom_ad_text=text)
        context.user_data.clear()
        await update.effective_message.reply_text("✅ Özel reklam metniniz kaydedildi!", reply_markup=get_keyboard(uid))

    elif flow == "blacklist":
        target = "@" + text.replace("https://t.me/", "").replace("t.me/", "").strip().lstrip("@").lower()
        if uid not in blacklisted_groups: blacklisted_groups[uid] = set()
        blacklisted_groups[uid].add(target)
        context.user_data.clear()
        await update.effective_message.reply_text(f"✅ Engellendi: {target}", reply_markup=get_keyboard(uid))

    elif flow in ["interval_group", "interval_loop"]:
        try:
            val = int(text)
            db.set_user(uid, **({ "interval_seconds": val } if flow == "interval_group" else { "loop_interval_seconds": val }))
            context.user_data.clear()
            await update.effective_message.reply_text(f"✅ Süre güncellendi: {val}sn", reply_markup=get_keyboard(uid))
        except ValueError:
            await update.effective_message.reply_text("Sayı girin.")

    elif flow == "redeem":
        ok, msg = db.use_license(uid, text)
        context.user_data.clear()
        await update.effective_message.reply_text(msg, reply_markup=get_keyboard(uid))

    elif flow == "key_days" and is_admin(uid):
        try:
            days = int(text)
            code = "FKD-" + "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(10))
            db.create_license(code, days)
            context.user_data.clear()
            await update.effective_message.reply_text(f"✅ KEY: `{code}` ({days} Gün)", parse_mode="Markdown", reply_markup=admin_keyboard())
        except ValueError:
            await update.effective_message.reply_text("Gün sayısı girin.")

def main():
    db.init_db()
    t = Thread(target=run_web)
    t.daemon = True
    t.start()
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    app.add_handler(MessageHandler(filters.PHOTO, text_handler))
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
