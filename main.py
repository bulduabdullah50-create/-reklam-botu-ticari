import os
import re
import asyncio
import logging
import secrets
import string
from threading import Thread
from flask import Flask
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError, FloodWaitError
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters
import database as db

app_web = Flask(__name__)
@app_web.route('/')
def home_web(): return "💎 FARKETMEZDA PLATINUM BOT AKTİF VE GÜVENLİ!"
def run_web(): app_web.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("farketmezda")

BOT_TOKEN = os.environ["BOT_TOKEN"]
API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
ADMIN_ID = int(os.environ["ADMIN_ID"])

clients, pending, tasks, blacklisted_groups, account_blacklists = {}, {}, {}, {}, {}

def is_admin(uid): return int(uid) == ADMIN_ID
def has_valid_access(uid): return is_admin(uid) or db.license_valid(uid, ADMIN_ID)

# Canlı, Göze Deçar ve Zengin Arayüz Tasarımı
def get_keyboard(uid):
    valid = has_valid_access(uid)
    status_text = "🟢 LİSANS AKTİF" if valid else "🔴 LİSANS YOK / SÜRE BİTTİ"
    
    rows = [
        [InlineKeyboardButton("💎 ABONELİK / LİSANS DURUMU", callback_data="status_panel")],
        [InlineKeyboardButton("➕ Hesap Ekle", callback_data="add"), InlineKeyboardButton("👤 Hesaplarım", callback_data="accounts")],
        [InlineKeyboardButton("📩 Reklam Bilgisi", callback_data="ad"), InlineKeyboardButton("🚫 Grup Engel Listesi", callback_data="blacklist")],
        [InlineKeyboardButton("⏱ Grup Bekleme Suresi", callback_data="time_group"), InlineKeyboardButton("⏳ Tur Dinlenme Süresi", callback_data="time_loop")],
        [InlineKeyboardButton("🔑 KEY (Lisans) Aktif Et", callback_data="redeem")],
        [InlineKeyboardButton("💬 Destek & İletişim", url="https://t.me/farketmezda")]
    ]
    if is_admin(uid):
        rows.append([InlineKeyboardButton("👑 YÖNETİCİ KONTROL PANELİ", callback_data="admin")])
    return InlineKeyboardMarkup(rows)

def sub_menu_keyboard(): return InlineKeyboardMarkup([[InlineKeyboardButton("⬅ Geri / Ana Menü", callback_data="home")]])
def admin_keyboard(): return InlineKeyboardMarkup([
    [InlineKeyboardButton("➕ KEY (Lisans) Üret", callback_data="key_create")],
    [InlineKeyboardButton("📋 Üretilen KEY Listesi", callback_data="key_list")],
    [InlineKeyboardButton("⬅ Ana Menü", callback_data="home")]
])

async def show_home(update, context):
    uid = update.effective_user.id
    db.ensure_user(uid, update.effective_user.username)
    text = (
        "✨ **FARKETMEZDA PREMIUM REKLAM OTOMASYONU** ✨\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🚀 *Sınırsız Güç, Kusursuz Dağıtım Altyapısı*\n\n"
        "✨ Sistem tamamen izole ve güvenlidir. Hesaplarınız asla silinmez, "
        "bakım veya kesinti sonrasında sistem otomatik olarak kaldığı yerden devam eder.\n\n"
        "👇 Aşağıdaki zengin menüden işlemlerinizi yönetebilirsiniz:"
    )
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard(uid))
    else:
        await update.effective_message.reply_text(text, parse_mode="Markdown", reply_markup=get_keyboard(uid))

async def start(update, context):
    context.user_data.clear()
    await show_home(update, context)

# --- REKLAM GÖNDERİM MOTORU (Kusursuz, Hata Korumalı, Otomatik Devam Eden) ---
async def start_sender(uid, message_obj, context):
    if not has_valid_access(uid):
        await message_obj.reply_text("❌ Aktif bir lisansınız bulunmuyor! Lütfen 'KEY Aktif Et' butonunu kullanarak lisans giriniz.")
        return
    
    task_key = f"{uid}_auto_all_accounts"
    if task_key in tasks and not tasks[task_key].done():
        await message_obj.reply_text("⚠️ Otomatik gönderim döngüsü zaten aktif ve çalışıyor!")
        return

    db.set_user(uid, is_running=1)
    tasks[task_key] = asyncio.create_task(sender_loop(uid, context, message_obj.chat_id))
    await message_obj.reply_text("🚀 **Otomasyon Başlatıldı!**\nSistem arıza veya kesintilerden etkilenmeden gruplara akıllı tur atmaya başladı.")

async def sender_loop(uid, context, chat_id):
    task_key = f"{uid}_auto_all_accounts"
    while True:
        try:
            if not has_valid_access(uid):
                db.set_user(uid, is_running=0)
                try: await context.bot.send_message(chat_id=chat_id, text="❌ Lisans süreniz dolduğu için otomasyon durduruldu.")
                except: pass
                break
            
            user_data = db.get_user(uid)
            if not user_data or user_data.get("is_running", 0) == 0:
                break

            db_sessions = db.get_user_sessions_db(uid)
            active_sessions = [(s[0], s[1], s[2]) for s in db_sessions if s[2] and len(s[2].strip()) > 50]

            if not active_sessions:
                try: await context.bot.send_message(chat_id=chat_id, text="⚠️ Sistemde aktif hesabınız bulunamadı. Lütfen 'Hesap Ekle' ile hesap tanımlayın.")
                except: pass
                await asyncio.sleep(20)
                continue

            loop_interval = user_data.get("loop_interval_seconds", 240)
            group_interval = user_data.get("interval_seconds", 3)

            for s_name, phone, s_string in active_sessions:
                if task_key not in tasks or db.get_user(uid).get("is_running", 0) == 0: 
                    break
                
                client = None
                try:
                    clean_string = s_string.strip().strip('"').strip("'").replace("\n", "").strip()
                    client = TelegramClient(StringSession(clean_string), API_ID, API_HASH, connection_retries=3, auto_reconnect=True, timeout=30)
                    await client.connect()
                    
                    if not await client.is_user_authorized():
                        try: await context.bot.send_message(chat_id=chat_id, text=f"❌ [{phone}] nolu hesabın oturumu düşmüş! Lütfen hesabı silip tekrar ekleyin.")
                        except: pass
                        await client.disconnect()
                        continue
                except Exception as e:
                    log.error(f"Bağlantı hatası: {e}")
                    continue

                try:
                    messages = await client.get_messages("me", limit=1)
                    if not messages:
                        try: await context.bot.send_message(chat_id=chat_id, text=f"⚠️ [{phone}] Hesabının Kayıtlı Mesajlar (Saved Messages) kısmı boş! Gönderilecek reklam bulunamadı.")
                        except: pass
                        await client.disconnect()
                        continue
                    source_msg = messages[0]
                except Exception as e:
                    try: await context.bot.send_message(chat_id=chat_id, text=f"❌ [{phone}] Kayıtlı mesajlar alınamadı: {e}")
                    except: pass
                    await client.disconnect()
                    continue

                ad_text = getattr(source_msg, 'text', '') or getattr(source_msg, 'message', '') or "Reklam Metni"
                
                try:
                    groups = []
                    async for dialog in client.iter_dialogs():
                        if dialog.is_group or (dialog.is_channel and getattr(dialog.entity, 'megagroup', False)):
                            groups.append(dialog)
                except Exception as e:
                    try: await context.bot.send_message(chat_id=chat_id, text=f"❌ [{phone}] Gruplar taranamadı: {e}")
                    except: pass
                    await client.disconnect()
                    continue

                if not groups:
                    try: await context.bot.send_message(chat_id=chat_id, text=f"⚠️ [{phone}] Bu hesapta taranabilir grup bulunamadı!")
                    except: pass
                    await client.disconnect()
                    continue

                user_bl = blacklisted_groups.get(uid, set())
                acc_bl = account_blacklists.get(s_name, set())
                success_count, fail_count, skipped_count = 0, 0, 0

                for group in groups:
                    if task_key not in tasks: break
                    g_id, entity = str(group.id), getattr(group, 'entity', None)
                    g_username = f"@{entity.username}".lower() if entity and getattr(entity, 'username', None) else ""

                    if g_id in user_bl or (g_username and g_username in user_bl) or g_id in acc_bl or (g_username and g_username in acc_bl):
                        skipped_count += 1
                        continue
                    try:
                        await client.send_message(group.id, ad_text)
                        success_count += 1
                    except FloodWaitError as exc:
                        await asyncio.sleep(exc.seconds + 5)
                        try:
                            await client.send_message(group.id, ad_text)
                            success_count += 1
                        except:
                            fail_count += 1
                    except Exception:
                        fail_count += 1
                    
                    if group_interval > 0: 
                        await asyncio.sleep(group_interval)

                try:
                    await context.bot.send_message(chat_id=chat_id, text=f"📊 **[{phone}] Turu Tamamlandı!**\n\n✅ Başarılı: {success_count}\n❌ Hatalı: {fail_count}\n🚫 Engellenen/Atlanan: {skipped_count}")
                except: pass

                try: await client.disconnect()
                except: pass

                await asyncio.sleep(3)

            # Dinlenme Süresi
            try:
                await context.bot.send_message(chat_id=chat_id, text=f"⏳ Tüm hesaplar turu tamamladı. Dinlenme süresi ({loop_interval} sn) bekleniyor...")
            except: pass
            await asyncio.sleep(loop_interval)

        except asyncio.CancelledError:
            break
        except Exception as e:
            log.exception("Döngü kritik hata: %s", e)
            await asyncio.sleep(15) # Hata olsa bile bot çökmez, 15 saniye sonra kaldığı yerden devam eder!
            
    tasks.pop(task_key, None)

async def stop_sender(uid, chat_id, context):
    db.set_user(uid, is_running=0)
    task_key = f"{uid}_auto_all_accounts"
    task = tasks.get(task_key)
    if task and not task.done():
        task.cancel()
        tasks.pop(task_key, None)
    await context.bot.send_message(chat_id=chat_id, text="⏹ Otomasyon başarıyla durduruldu.")

# --- CALLBACK VE ARAYÜZ YÖNETİMİ ---
async def button(update, context):
    q = update.callback_query
    await q.answer()
    uid, action = q.from_user.id, q.data
    db.ensure_user(uid, q.from_user.username)

    if action == "home": 
        await q.edit_message_text("✨ FARKETMEZDA PREMIUM PANEL", reply_markup=get_keyboard(uid))
    
    elif action == "status_panel":
        valid = has_valid_access(uid)
        status_str = "🟢 Aktif ve Sınırsız / Lisanslı" if valid else "🔴 Lisans Bulunamadı"
        text = f"💎 **ABONELİK VE LİSANS BİLGİLERİ**\n\nKullanıcı ID: `{uid}`\nDurum: {status_str}\n\nKEY kullanarak sürenizi uzatabilirsiniz."
        await q.edit_message_text(text, parse_mode="Markdown", reply_markup=sub_menu_keyboard())

    elif action == "add":
        if not has_valid_access(uid):
            await q.edit_message_text("❌ İşlem reddedildi. Aktif bir lisansa ihtiyacınız var.", reply_markup=sub_menu_keyboard())
            return
        context.user_data["flow"] = "phone"
        await q.edit_message_text("➕ Lütfen hesabınızın telefon numarasını + ülke kodu ile gönderin:\n*Örnek:* `+905554443322`", parse_mode="Markdown", reply_markup=sub_menu_keyboard())

    elif action == "accounts":
        db_sessions = db.get_user_sessions_db(uid)
        if not db_sessions:
            await q.edit_message_text("👤 **HESAPLARIM**\n\nSistemde kayıtlı hesabınız yok.", parse_mode="Markdown", reply_markup=get_keyboard(uid))
            return
        rows = [[InlineKeyboardButton(f"📱 {phone}", callback_data=f"manage_{s_name}")] for s_name, phone, _ in db_sessions]
        rows.append([InlineKeyboardButton("⬅ Ana Menü", callback_data="home")])
        await q.edit_message_text("👤 **HESAPLARIM**\nYönetmek istediğiniz hesabı seçin:", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(rows))

    elif action.startswith("manage_"):
        s_name = action.replace("manage_", "")
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("▶ Otomasyonu Başlat", callback_data=f"start_run")],
            [InlineKeyboardButton("⏹ Otomasyonu Durdur", callback_data=f"stop_run")],
            [InlineKeyboardButton("❌ Hesabı Sil", callback_data=f"del_{s_name}")],
            [InlineKeyboardButton("⬅ Geri", callback_data="accounts")]
        ])
        await q.edit_message_text("📱 **Hesap Yönetim Paneli**\nAşağıdan komut verebilirsiniz:", parse_mode="Markdown", reply_markup=kb)

    elif action == "start_run":
        await start_sender(uid, q.message, context)

    elif action == "stop_run":
        await stop_sender(uid, q.message.chat_id, context)

    elif action.startswith("del_"):
        s_name = action.replace("del_", "")
        db.delete_session_db(s_name)
        clients.pop(s_name, None)
        await q.edit_message_text("✅ Hesap sistemden silindi.", reply_markup=get_keyboard(uid))

    elif action == "ad":
        await q.edit_message_text("ℹ️ **Reklam Kaynağı:**\nBot, bağladığınız Telegram hesabının **'Kayıtlı Mesajlar' (Saved Messages)** kısmındaki en son mesajı otomatik olarak alır ve gruplara basar.", parse_mode="Markdown", reply_markup=sub_menu_keyboard())

    elif action == "blacklist":
        context.user_data["flow"] = "blacklist"
        await q.edit_message_text("🚫 Engellemek istediğiniz grubun linkini veya kullanıcı adını gönderin:", reply_markup=sub_menu_keyboard())

    elif action == "time_group":
        context.user_data["flow"] = "interval_group"
        await q.edit_message_text("⏱ İki mesaj gönderimi arasında kaç saniye beklensin? (Sayı olarak girin, örn: 3):", reply_markup=sub_menu_keyboard())

    elif action == "time_loop":
        context.user_data["flow"] = "interval_loop"
        await q.edit_message_text("⏳ Tüm gruplar bittikten sonra yeni tur için kaç saniye dinlenilsin? (Sayı olarak girin, örn: 240):", reply_markup=sub_menu_keyboard())

    elif action == "redeem":
        context.user_data["flow"] = "redeem"
        await q.edit_message_text("🔑 Lütfen satın aldığınız KEY kodunu girin:", reply_markup=sub_menu_keyboard())

    elif action == "admin" and is_admin(uid):
        await q.edit_message_text("👑 **YÖNETİCİ KONTROL PANELİ**", parse_mode="Markdown", reply_markup=admin_keyboard())

    elif action == "key_create" and is_admin(uid):
        context.user_data["flow"] = "key_days"
        await q.edit_message_text("Kaç günlük KEY üretilsin? (Sadece gün sayısı girin, örn: 30):", reply_markup=sub_menu_keyboard())

    elif action == "key_list" and is_admin(uid):
        rows = db.list_licenses()
        txt = "📋 **ÜRETİLEN KEY LİSTESİ**\n\n"
        txt += "\n".join(f"`{r['code']}` | {r['duration_days']} Gün | Kullanıldı: {'Evet ❌' if r['is_used'] else 'Hayır ✅'}" for r in rows) if rows else "Henüz KEY üretilmemiş."
        await q.edit_message_text(txt[:3900], parse_mode="Markdown", reply_markup=admin_keyboard())

# --- METİN VE GİRİŞ İŞLEMLERİ (Telefon, Kod, KEY, Aralıklar) ---
async def text_handler(update, context):
    uid, text = update.effective_user.id, update.effective_message.text.strip()
    flow = context.user_data.get("flow")
    if not flow: return

    if flow == "phone":
        if not re.fullmatch(r"\+[1-9]\d{7,14}", text):
            await update.effective_message.reply_text("Geçerli bir telefon numarası girin (+90...)")
            return
        session_name = f"SESSION_{uid}_{text}"
        client = TelegramClient(StringSession(), API_ID, API_HASH, connection_retries=2, auto_reconnect=True)
        await client.connect()
        try:
            sent = await client.send_code_request(text)
            pending[uid] = {"client": client, "phone": text, "hash": sent.phone_code_hash, "session_name": session_name}
            context.user_data["flow"] = "code"
            await update.effective_message.reply_text("📲 Telegram uygulamanıza gelen doğrulama kodunu buraya gönderin:")
        except Exception as e:
            await client.disconnect()
            await update.effective_message.reply_text(f"❌ Kod gönderilemedi: {e}")

    elif flow == "code":
        item = pending.get(uid)
        if not item:
            context.user_data.clear()
            await update.effective_message.reply_text("Süre aşımı. Lütfen baştan başlayın.")
            return
        try:
            await item["client"].sign_in(phone=item["phone"], code=text, phone_code_hash=item["hash"])
            s_name = item["session_name"]
            s_string = item["client"].session.save()
            clients[s_name] = item["client"]
            db.save_session_db(uid, s_name, item["phone"], s_string)
            pending.pop(uid, None)
            context.user_data.clear()
            await update.effective_message.reply_text("✅ Hesap başarıyla bağlandı ve şifreli olarak kaydedildi!", reply_markup=get_keyboard(uid))
        except SessionPasswordNeededError:
            context.user_data["flow"] = "2fa"
            await update.effective_message.reply_text("🔒 Hesabınızda İki Aşamalı Doğrulama (2FA) var. Lütfen şifrenizi girin:")
        except Exception as e:
            await update.effective_message.reply_text(f"❌ Giriş hatası: {e}")

    elif flow == "2fa":
        item = pending.get(uid)
        if not item:
            context.user_data.clear()
            await update.effective_message.reply_text("İşlem zaman aşımına uğradı.")
            return
        try:
            await item["client"].sign_in(password=text)
            s_name = item["session_name"]
            s_string = item["client"].session.save()
            clients[s_name] = item["client"]
            db.save_session_db(uid, s_name, item["phone"], s_string)
            pending.pop(uid, None)
            context.user_data.clear()
            await update.effective_message.reply_text("✅ 2FA şifresi doğrulandı, hesap kaydedildi!", reply_markup=get_keyboard(uid))
        except Exception as e:
            await update.effective_message.reply_text(f"❌ Şifre hatalı: {e}")

    elif flow == "blacklist":
        target = "@" + text.replace("https://t.me/", "").replace("t.me/", "").strip().lstrip("@").lower()
        if uid not in blacklisted_groups: blacklisted_groups[uid] = set()
        blacklisted_groups[uid].add(target)
        context.user_data.clear()
        await update.effective_message.reply_text(f"✅ Engelli listesine eklendi: {target}", reply_markup=get_keyboard(uid))

    elif flow in ["interval_group", "interval_loop"]:
        try:
            val = int(text)
            db.set_user(uid, **({ "interval_seconds": val } if flow == "interval_group" else { "loop_interval_seconds": val }))
            context.user_data.clear()
            await update.effective_message.reply_text(f"✅ Süre başarıyla güncellendi: {val} saniye", reply_markup=get_keyboard(uid))
        except ValueError:
            await update.effective_message.reply_text("Lütfen geçerli bir sayı girin.")

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
            await update.effective_message.reply_text(f"✅ **KEY Başarıyla Üretildi:**\n\n`{code}`\nSüre: {days} Gün", parse_mode="Markdown", reply_markup=admin_keyboard())
        except ValueError:
            await update.effective_message.reply_text("Lütfen geçerli bir gün sayısı girin.")

def main():
    db.init_db()
    t = Thread(target=run_web)
    t.daemon = True
    t.start()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
