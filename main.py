import os
import re
import asyncio
import secrets
import string
import logging
import gc
from datetime import datetime
from threading import Thread
from flask import Flask
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError, PhoneCodeInvalidError, PhoneCodeExpiredError, PasswordHashInvalidError, FloodWaitError
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters
import database as db

app_web = Flask(__name__)
@app_web.route('/')
def home_web(): return "Bot aktif ve kararlı sürümde çalışıyor!"
def run_web(): app_web.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("farketmezda")

BOT_TOKEN = os.environ["BOT_TOKEN"]
API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
ADMIN_ID = int(os.environ["ADMIN_ID"])

clients, pending, tasks, blacklisted_groups, account_blacklists, tur_detaylari = {}, {}, {}, {}, {}, {}

def is_admin(uid): return int(uid) == ADMIN_ID
def has_valid_access(uid): return is_admin(uid) or db.license_valid(uid)

def get_keyboard(uid):
    rows = [[InlineKeyboardButton("➕ Hesap Ekle", callback_data="add"), InlineKeyboardButton("👤 Hesaplarım", callback_data="accounts")],
            [InlineKeyboardButton("📩 Reklam Seç", callback_data="ad"), InlineKeyboardButton("🚫 Genel Grup Engel", callback_data="blacklist")],
            [InlineKeyboardButton("⏱ Grup Aralığı", callback_data="time_group"), InlineKeyboardButton("⏳ Tur Süresi", callback_data="time_loop")],
            [InlineKeyboardButton("📊 Durum", callback_data="status"), InlineKeyboardButton("🔑 KEY Kullan", callback_data="redeem")],
            [InlineKeyboardButton("💬 İletişim", url="https://t.me/farketmezda")]]
    if is_admin(uid): rows.append([InlineKeyboardButton("👑 Yönetici Paneli", callback_data="admin")])
    return InlineKeyboardMarkup(rows)

def sub_menu_keyboard(): return InlineKeyboardMarkup([[InlineKeyboardButton("⬅ Geri / Ana Menü", callback_data="home")]])
def admin_keyboard(): return InlineKeyboardMarkup([[InlineKeyboardButton("➕ KEY Oluştur", callback_data="key_create")], [InlineKeyboardButton("📋 KEY Listesi", callback_data="key_list")], [InlineKeyboardButton("⬅ Ana Menü", callback_data="home")]])

async def get_client_by_session_string(session_name, s_string):
    client = clients.get(session_name)
    if client:
        try:
            if not client.is_connected(): await client.connect()
            if await client.is_user_authorized(): return client
            else: clients.pop(session_name, None)
        except Exception: clients.pop(session_name, None)

    try:
        clean_string = s_string.strip().strip('"').strip("'").replace("\n", "").replace("\r", "").strip()
        client = TelegramClient(StringSession(clean_string), API_ID, API_HASH, connection_retries=2, auto_reconnect=True, timeout=20)
        await client.connect()
        if not await client.is_user_authorized():
            await client.disconnect()
            return None
        clients[session_name] = client
        return client
    except Exception as e:
        log.error("Oturum çözülemedi [%s]: %s", session_name, e)
        return None

async def show_home(update, context):
    uid = update.effective_user.id
    db.ensure_user(uid, update.effective_user.username)
    text = "💜 FARKETMEZDA\n━━━━━━━━━━━━━━━━━━\nNEON YÖNETİM PANELİ\n\nHoş geldin!\nİşlem yapmak için menüden seçim yap."
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, reply_markup=get_keyboard(uid))
    else:
        await update.effective_message.reply_text(text, reply_markup=get_keyboard(uid))

async def start(update, context):
    context.user_data.clear()
    await show_home(update, context)

async def start_sender(uid, session_name, message_obj, context):
    if not has_valid_access(uid):
        await message_obj.reply_text("❌ Aktif lisansın bulunmuyor! Lütfen geçerli bir KEY kullan.")
        return
    task_key = f"{uid}_auto_all_accounts"
    current = tasks.get(task_key)
    if current and not current.done():
        await message_obj.reply_text("⚠️ Otomatik gönderim sistemi zaten arka planda çalışıyor.")
        return
    tasks[task_key] = asyncio.create_task(sender_loop(uid, context, message_obj.chat_id))
    await message_obj.reply_text("▶ **Otomatik Çoklu Hesap Motoru Başlatıldı!** (Sadece Gruplar)")

async def sender_loop(uid, context, chat_id):
    task_key = f"{uid}_auto_all_accounts"
    while True:
        try:
            if not has_valid_access(uid): break
            user = db.get_user(uid)
            
            db_sessions = db.get_user_sessions_db(uid)
            active_sessions = [(s[0], s[1]) for s in db_sessions if s[1] and len(s[1].strip()) > 50]
            
            for k, v in os.environ.items():
                if k.startswith("SESSION_") and v and len(v.strip()) > 50:
                    if not any(item[0] == k for item in active_sessions):
                        active_sessions.append((k, v))

            if not active_sessions:
                try: await context.bot.send_message(chat_id=chat_id, text="❌ Aktif hesap bulunamadı! Lütfen 'Hesap Ekle' ile hesap bağlayın.")
                except Exception: pass
                await asyncio.sleep(15)
                continue

            loop_interval = user.get("loop_interval_seconds", 240) if user and user.get("loop_interval_seconds") is not None else 240
            group_interval = user.get("interval_seconds", 3) if user and user.get("interval_seconds") is not None else 3

            for s_name, s_string in active_sessions:
                if task_key not in tasks: break
                
                client = await get_client_by_session_string(s_name, s_string)
                if not client:
                    continue

                try:
                    if not client.is_connected(): await client.connect()
                    messages = await client.get_messages("me", limit=1)
                    if not messages:
                        try: await context.bot.send_message(chat_id=chat_id, text=f"❌ '{s_name}' hesabının Kayıtlı Mesajlar'ı boş!")
                        except Exception: pass
                        await client.disconnect()
                        continue
                    source_msg = messages[0]
                except Exception as e:
                    log.exception("Kayıtlı mesaj alınamadı (%s): %s", s_name, e)
                    try: await client.disconnect()
                    except Exception: pass
                    continue

                ad_text = getattr(source_msg, 'text', '') or getattr(source_msg, 'message', '') or "Reklam İçeriği"
                
                try:
                    groups = []
                    async for dialog in client.iter_dialogs():
                        if dialog.is_group or dialog.is_supergroup:
                            groups.append(dialog)
                except Exception as e:
                    log.exception("Gruplar çekilirken hata (%s): %s", s_name, e)
                    try: await client.disconnect()
                    except Exception: pass
                    continue

                if not groups:
                    try: await context.bot.send_message(chat_id=chat_id, text=f"⚠️ [{s_name}] Hiç grup bulunamadı! Atlanıyor...")
                    except Exception: pass
                    try: await client.disconnect()
                    except Exception: pass
                    continue

                user_bl = blacklisted_groups.get(uid, set())
                acc_bl = account_blacklists.get(s_name, set())
                success_count, fail_count, skipped_count, detay_listesi = 0, 0, 0, []

                try:
                    await context.bot.send_message(chat_id=chat_id, text=f"🚀 **[{s_name}]** hesabı gruplara tur başlattı! (Hedef: {len(groups)} grup)")
                except Exception: pass

                for group in groups:
                    if task_key not in tasks: break
                    g_id, entity = str(group.id), getattr(group, 'entity', None)
                    g_username = f"@{entity.username}".lower() if entity and getattr(entity, 'username', None) else ""
                    g_title = getattr(group, 'name', 'İsimsiz Grup')

                    if g_id in user_bl or (g_username and g_username in user_bl) or g_id in acc_bl or (g_username and g_username in acc_bl):
                        skipped_count += 1
                        detay_listesi.append(f"🚫 Engellendi/Atlandı: {g_title}")
                        continue
                    try:
                        await client.send_message(group.id, ad_text)
                        success_count += 1
                        detay_listesi.append(f"✅ Başarılı: {g_title}")
                    except FloodWaitError as exc:
                        log.warning("Flood sınırı: %s sn", exc.seconds)
                        detay_listesi.append(f"⏳ Flood Sınırı ({exc.seconds}sn): {g_title}")
                        await asyncio.sleep(exc.seconds + 5)
                    except Exception as e:
                        fail_count += 1
                        detay_listesi.append(f"❌ Hatalı: {g_title}")
                    
                    if group_interval > 0: await asyncio.sleep(group_interval)

                tur_detaylari[s_name] = detay_listesi
                try:
                    report_kb = InlineKeyboardMarkup([[InlineKeyboardButton("📋 Detaylı Raporu Gör", callback_data=f"report_{s_name}")]])
                    await context.bot.send_message(chat_id=chat_id, text=f"📊 **[{s_name}] Hesabının Turu Tamamlandı!**\n\n✅ Başarılı: {success_count}\n❌ Hatalı: {fail_count}\n🚫 Engellenen: {skipped_count}", reply_markup=report_kb)
                except Exception: pass

                try:
                    if client.is_connected(): await client.disconnect()
                except Exception: pass
                
                del groups
                gc.collect()
                await asyncio.sleep(3)

            fresh_user = db.get_user(uid)
            current_loop_interval = fresh_user.get("loop_interval_seconds", 240) if fresh_user and fresh_user.get("loop_interval_seconds") is not None else 240

            try:
                await context.bot.send_message(chat_id=chat_id, text=f"⏳ Tüm hesaplar turunu tamamladı. Belirttiğin süre olan {current_loop_interval} saniye bekleniyor...")
            except Exception: pass
            
            gc.collect()
            if current_loop_interval > 0: await asyncio.sleep(current_loop_interval)

        except asyncio.CancelledError:
            break
        except Exception as e:
            log.exception("Döngü hatası: %s", e)
            await asyncio.sleep(10)
            
    tasks.pop(task_key, None)

async def stop_sender(uid, session_name, chat_id, context):
    task_key = f"{uid}_auto_all_accounts"
    task = tasks.get(task_key)
    if task and not task.done():
        task.cancel()
        tasks.pop(task_key, None)
        await context.bot.send_message(chat_id=chat_id, text="⏹ Tüm hesapların otomatik gönderim döngüsü durduruldu.")
    else:
        await context.bot.send_message(chat_id=chat_id, text="⚠️ Zaten çalışan aktif bir gönderim bulunmuyor.")

async def button(update, context):
    q = update.callback_query
    await q.answer()
    uid, action = q.from_user.id, q.data
    db.ensure_user(uid, q.from_user.username)

    if action == "home": await q.edit_message_text("💜 FARKETMEZDA NEON PANEL", reply_markup=get_keyboard(uid))
    elif action == "add":
        if not has_valid_access(uid):
            await q.edit_message_text("❌ **Erişim Engellendi!**\nHesap eklemek için aktif lisansınızın bulunması gerekir.", reply_markup=sub_menu_keyboard())
            return
        context.user_data["flow"] = "phone"
        await q.edit_message_text("➕ YENİ HESAP EKLE\n\nEklenecek Telegram numaranı ülke koduyla gönder.\nÖrnek: +905xxxxxxxxx", reply_markup=sub_menu_keyboard())
    elif action == "accounts":
        db_sessions = db.get_user_sessions_db(uid)
        if not db_sessions:
            await q.edit_message_text("👤 HESAPLARIM\n\nHenüz kayıtlı hesap bulunmuyor.", reply_markup=get_keyboard(uid))
            return
        keyboard_rows = [[InlineKeyboardButton(f"📱 {s_name.replace('SESSION_', '')}", callback_data=f"manage_{s_name}")] for s_name, _ in db_sessions]
        keyboard_rows.append([InlineKeyboardButton("⬅ Ana Menü", callback_data="home")])
        await q.edit_message_text("👤 HESAPLARIM\n\nYönetmek istediğin hesaba tıkla:", reply_markup=InlineKeyboardMarkup(keyboard_rows))
    elif action.startswith("manage_"):
        session_name = action.replace("manage_", "")
        phone_num = session_name.replace("SESSION_", "")
        is_running = f"{uid}_auto_all_accounts" in tasks and not tasks[f"{uid}_auto_all_accounts"].done()
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("▶ Başlat (Tüm Hesaplar)", callback_data=f"start_{session_name}"), InlineKeyboardButton("⏹ Durdur", callback_data=f"stop_{session_name}")],
            [InlineKeyboardButton("🚫 Bu Hesaba Özel Grup Engel", callback_data=f"acc_bl_{session_name}")],
            [InlineKeyboardButton("❌ Bu Hesabı Sil", callback_data=f"del_{session_name}")],
            [InlineKeyboardButton("⬅ Hesaplara Dön", callback_data="accounts")]
        ])
        await q.edit_message_text(f"📱 **Hesap Yönetimi:** `{phone_num}`\n\nSistem Durumu: {'🟢 Çalışıyor' if is_running else '🔴 Duruyor'}", parse_mode="Markdown", reply_markup=kb)
    elif action.startswith("acc_bl_"):
        session_name = action.replace("acc_bl_", "")
        context.user_data["flow"] = f"acc_blacklist_{session_name}"
        await q.edit_message_text(f"🚫 **HESABA ÖZEL ENGELLEME** (`{session_name}`)\n\nEngelleyeceğin grubun linkini veya kullanıcı adını gönder.", reply_markup=sub_menu_keyboard())
    elif action.startswith("report_"):
        session_name = action.replace("report_", "")
        await q.message.reply_text(f"📋 **{session_name} Son Tur Detayları**\n\n" + "\n".join(tur_detaylari.get(session_name, ["Henüz detay bulunmuyor."]))[:3950])
    elif action.startswith("start_"): await start_sender(uid, action.replace("start_", ""), q.message, context)
    elif action.startswith("stop_"): await stop_sender(uid, action.replace("stop_", ""), q.message.chat_id, context)
    elif action.startswith("del_"):
        session_name = action.replace("del_", "")
        if f"{uid}_auto_all_accounts" in tasks: tasks.pop(f"{uid}_auto_all_accounts").cancel()
        clients.pop(session_name, None)
        db.delete_session_db(session_name)
        await q.edit_message_text(f"✅ **{session_name}** hesabı silindi.", reply_markup=get_keyboard(uid))
    elif action == "ad": await q.edit_message_text("ℹ️ Her hesap reklamı doğrudan **kendi Kayıtlı Mesajlar'ındakinden** alır.", reply_markup=get_keyboard(uid))
    elif action == "blacklist":
        context.user_data["flow"] = "blacklist"
        await q.edit_message_text("🚫 GENEL GRUP ENGELLEME\nTüm hesaplarda engellenecek grubun linkini veya kullanıcı adını gönder.", reply_markup=sub_menu_keyboard())
    elif action == "time_group":
        context.user_data["flow"] = "interval_group"
        await q.edit_message_text(f"⏱ Grup Aralığı: {(db.get_user(uid) or {}).get('interval_seconds', 3)} saniye\nYeni saniyeyi gönder:", reply_markup=sub_menu_keyboard())
    elif action == "time_loop":
        context.user_data["flow"] = "interval_loop"
        await q.edit_message_text(f"⏳ Tur Süresi: {(db.get_user(uid) or {}).get('loop_interval_seconds', 240)} saniye\nYeni süreyi saniye olarak gönder:", reply_markup=sub_menu_keyboard())
    elif action == "redeem":
        context.user_data["flow"] = "redeem"
        await q.edit_message_text("🔑 KEY kodunu gönder.", reply_markup=sub_menu_keyboard())
    elif action == "status":
        db_sessions = db.get_user_sessions_db(uid)
        await q.edit_message_text(f"📊 SİSTEM DURUMU\n\nKayıtlı Hesap Sayısı: {len(db_sessions)}\nLisans Durumu: Aktif", reply_markup=get_keyboard(uid))
    elif action == "admin" and is_admin(uid): await q.edit_message_text("👑 YÖNETİCİ PANELİ", reply_markup=admin_keyboard())
    elif action == "key_create" and is_admin(uid):
        context.user_data["flow"] = "key_days"
        await q.edit_message_text("KEY süresini gün olarak gönder. Örnek: 30", reply_markup=sub_menu_keyboard())
    elif action == "key_list" and is_admin(uid):
        rows = db.list_licenses()
        text = "📋 KEY LİSTESİ\n\n" + ("\n".join(f"{r['code']} | {r['duration_days']} gün | {'Kullanıldı' if r['is_used'] else 'Boş'}" for r in rows) if rows else "Henüz KEY yok.")
        await q.edit_message_text(text[:3900], reply_markup=admin_keyboard())

async def text_handler(update, context):
    uid, text = update.effective_user.id, update.effective_message.text.strip()
    flow = context.user_data.get("flow")
    if not flow: return

    if flow.startswith("acc_blacklist_"):
        session_name = flow.replace("acc_blacklist_", "")
        target = "@" + text.replace("https://t.me/", "").replace("t.me/", "").strip().lstrip("@").lower()
        if session_name not in account_blacklists: account_blacklists[session_name] = set()
        account_blacklists[session_name].add(target)
        context.user_data.clear()
        await update.effective_message.reply_text(f"✅ **{target}** karalisteye eklendi.", parse_mode="Markdown", reply_markup=get_keyboard(uid))
    elif flow == "phone":
        if not re.fullmatch(r"\+[1-9]\d{7,14}", text):
            await update.effective_message.reply_text("Telefon numaranı +ülke koduyla gönder.")
            return
        session_name = f"SESSION_{uid}_{text}"
        client = TelegramClient(StringSession(), API_ID, API_HASH, connection_retries=3, auto_reconnect=True)
        await client.connect()
        try:
            sent = await client.send_code_request(text)
            pending[uid] = {"client": client, "phone": text, "hash": sent.phone_code_hash, "session_name": session_name}
            context.user_data["flow"] = "code"
            await update.effective_message.reply_text("Bu numaraya gelen Telegram doğrulama kodunu gönder:")
        except Exception as e:
            await client.disconnect()
            await update.effective_message.reply_text(f"Kod gönderilemedi: {e}")
    elif flow == "code":
        item = pending.get(uid)
        if not item:
            context.user_data.clear()
            await update.effective_message.reply_text("Süre bitti. /start ile yeniden başla.")
            return
        try:
            await item["client"].sign_in(phone=item["phone"], code=text, phone_code_hash=item["hash"])
            s_name = item["session_name"]
            s_string = item["client"].session.save()
            clients[s_name] = item["client"]
            db.save_session_db(uid, s_name, s_string)
            pending.pop(uid, None)
            context.user_data.clear()
            await update.effective_message.reply_text(f"✅ **{item['phone']}** hesabı başarıyla bağlandı ve kaydedildi!", reply_markup=get_keyboard(uid))
        except SessionPasswordNeededError:
            context.user_data["flow"] = "2fa"
            await update.effective_message.reply_text("İki adımlı doğrulama (2FA) şifreni gönder:")
        except (PhoneCodeInvalidError, PhoneCodeExpiredError):
            await update.effective_message.reply_text("Kod geçersiz veya süresi dolmuş.")
        except Exception as e:
            await update.effective_message.reply_text(f"Giriş tamamlanamadı: {e}")
    elif flow == "2fa":
        item = pending.get(uid)
        if not item:
            context.user_data.clear()
            await update.effective_message.reply_text("Süre bitti. /start ile yeniden başla.")
            return
        try:
            await item["client"].sign_in(password=text)
            s_name = item["session_name"]
            s_string = item["client"].session.save()
            clients[s_name] = item["client"]
            db.save_session_db(uid, s_name, s_string)
            pending.pop(uid, None)
            context.user_data.clear()
            await update.effective_message.reply_text(f"✅ Hesap başarıyla bağlandı ve kaydedildi!", reply_markup=get_keyboard(uid))
        except PasswordHashInvalidError:
            await update.effective_message.reply_text("Şifre hatalı. Tekrar dene.")
        except Exception as e:
            await update.effective_message.reply_text(f"Giriş tamamlanamadı: {e}")
    elif flow == "blacklist":
        target = "@" + text.replace("https://t.me/", "").replace("t.me/", "").strip().lstrip("@").lower()
        if uid not in blacklisted_groups: blacklisted_groups[uid] = set()
        blacklisted_groups[uid].add(target)
        context.user_data.clear()
        await update.effective_message.reply_text(f"✅ **{target}** genel karalisteye eklendi.", reply_markup=get_keyboard(uid))
    elif flow in ["interval_group", "interval_loop"]:
        try:
            val = int(text)
            if val < 0: raise ValueError
            db.set_user(uid, **({ "interval_seconds": val } if flow == "interval_group" else { "loop_interval_seconds": val }))
            context.user_data.clear()
            await update.effective_message.reply_text(f"✅ Süre başarıyla güncellendi: {val} saniye.", reply_markup=get_keyboard(uid))
        except ValueError:
            await update.effective_message.reply_text("Lütfen geçerli bir sayı gir.")
    elif flow == "redeem":
        ok, msg = db.use_license(uid, text)
        context.user_data.clear()
        await update.effective_message.reply_text(("✅ " if ok else "❌ ") + msg, reply_markup=get_keyboard(uid))
    elif flow == "key_days" and is_admin(uid):
        try:
            days = int(text)
            if not 1 <= days <= 3650: raise ValueError
            code = "FKD-" + "-".join("".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(4)) for _ in range(3))
            db.create_license(code, days)
            context.user_data.clear()
            await update.effective_message.reply_text(f"✅ KEY OLUŞTURULDU\n\n`{code}`\n\nSüre: {days} gün", parse_mode="Markdown", reply_markup=admin_keyboard())
        except ValueError:
            await update.effective_message.reply_text("1 ile 3650 arasında gün gir.")

async def shutdown(app):
    for item in list(pending.values()):
        try: await item["client"].disconnect()
        except Exception: pass
    for client in list(clients.values()):
        try: await client.disconnect()
        except Exception: pass

def main():
    db.init_db()
    t = Thread(target=run_web)
    t.daemon = True
    t.start()
    app = Application.builder().token(BOT_TOKEN).post_shutdown(shutdown).build()
    app.add_handler(CommandHandler("start", lambda u, c: start(u, c)))
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
