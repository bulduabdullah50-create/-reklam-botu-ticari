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
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters
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
    valid = has_valid_access(uid)
    expiry = db.get_user_expiry(uid)
    
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
    [InlineKeyboardButton("⬅ Ana Menü", callback_data="home")]
])

async def show_home(update, context):
    uid = update.effective_user.id
    db.ensure_user(uid, update.effective_user.username)
    valid = has_valid_access(uid)
    expiry_str = db.get_user_expiry(uid)
    
    text = (
        "✨ **FARKETMEZDA PLATINUM OTOMASYON** ✨\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 Kullanıcı ID: `{uid}`\n"
        f"🛡 Lisans Durumu: {'🟢 Aktif (Bitiş: ' + str(expiry_str)[:16] + ')' if valid else '🔴 Süre Yok / Bitti'}\n\n"
        "🚀 Sistem kesintisiz çalışır. Hesaplarınız asla silinmez, bakım veya çökme sonrasında otomatik olarak kaldığı yerden devam eder.\n"
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

# --- REKLAM GÖNDERİM MOTORU (Kesintisiz, Rapor Veren, Oto-Devam Eden) ---
async def start_sender(uid, message_obj, context):
    if not has_valid_access(uid):
        await message_obj.reply_text("❌ Aktif lisansınız bulunmuyor! Lütfen 'ABONELİK & FİYATLAR' menüsünden paket seçip ödeme yapın.")
        return
    
    task_key = f"{uid}_auto_all_accounts"
    if task_key in tasks and not tasks[task_key].done():
        await message_obj.reply_text("⚠️ Otomasyon döngüsü zaten aktif ve çalışıyor!")
        return

    db.set_user(uid, is_running=1)
    tasks[task_key] = asyncio.create_task(sender_loop(uid, context, message_obj.chat_id))
    await message_obj.reply_text("▶ **REKLAM OTOMASYONU BAŞLATILDI!**\nSadece gruplara hedefli gönderim yapılıyor.")

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
                try: await context.bot.send_message(chat_id=chat_id, text="⚠️ Sistemde aktif hesabınız yok. Lütfen hesap ekleyin.")
                except: pass
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
                        try: await context.bot.send_message(chat_id=chat_id, text=f"❌ [{phone}] Oturumu yetkisiz! Lütfen hesabı silip tekrar ekleyin.")
                        except: pass
                        await client.disconnect()
                        continue
                except Exception as e:
                    log.error(f"Bağlantı hatası: {e}")
                    continue

                # Reklam metnini belirleme (Özel girildiyse o, yoksa Saved Messages)
                ad_text = ""
                if custom_text:
                    ad_text = custom_text
                else:
                    try:
                        messages = await client.get_messages("me", limit=1)
                        if messages:
                            source_msg = messages[0]
                            ad_text = getattr(source_msg, 'text', '') or getattr(source_msg, 'message', '')
                    except:
                        pass
                
                if not ad_text:
                    try: await context.bot.send_message(chat_id=chat_id, text=f"⚠️ [{phone}] Gönderilecek reklam metni bulunamadı! (Kayıtlı mesajlar boş veya özel metin ayarlanmamış).")
                    except: pass
                    await client.disconnect()
                    continue

                # Sadece grupları tarama (Kanalları hariç tutma)
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
                acc_bl_ids = set(db.get_account_blacklist_db(s_name))
                success_count, fail_count, skipped_count = 0, 0, 0
                sent_details, failed_details = [], []

                try:
                    await context.bot.send_message(chat_id=chat_id, text=f"🚀 **[{phone}]** Hesabı reklam turuna başladı! ({len(groups)} grup taranıyor...)")
                except: pass

                for group in groups:
                    if task_key not in tasks: break
                    g_id, g_title = str(group.id), getattr(group, 'title', 'İsimsiz Grup')
                    entity = getattr(group, 'entity', None)
                    g_username = f"@{entity.username}".lower() if entity and getattr(entity, 'username', None) else ""

                    # Genel veya hesap bazlı engellenen grup kontrolü
                    if g_id in user_bl or (g_username and g_username in user_bl) or g_id in acc_bl_ids or g_id in acc_bl_ids:
                        skipped_count += 1
                        continue
                    
                    try:
                        # Premium simge ve emojiler dahil tam destekli gönderim
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
                            failed_details.append(g_title)
                    except Exception:
                        fail_count += 1
                        failed_details.append(g_title)
                    
                    if group_interval > 0: 
                        await asyncio.sleep(group_interval)

                # Detaylı Raporlama Mesajı
                report_text = (
                    f"📊 **[{phone}] REKLAM TURU TAMAMLANDI**\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"✅ Başarılı Gönderilen Grup Sayısı: {success_count}\n"
                    f"❌ Gönderilemeyen (Hatalı) Grup Sayısı: {fail_count}\n"
                    f"🚫 Atlanan / Engellenen Grup Sayısı: {skipped_count}\n\n"
                )
                if sent_details:
                    report_text += f"🟢 **Başarılı Atılanlar:**\n" + "\n".join([f"• {t}" for t in sent_details[:10]])
                    if len(sent_details) > 10: report_text += f"\n*(ve {len(sent_details)-10} grup daha...)*"
                
                try:
                    await context.bot.send_message(chat_id=chat_id, text=report_text[:3900], parse_mode="Markdown")
                except: pass

                try: await client.disconnect()
                except: pass

                await asyncio.sleep(3)

            # Tur dinlenme süresi
            try:
                await context.bot.send_message(chat_id=chat_id, text=f"⏳ Tüm hesaplar turunu tamamladı. {loop_interval} saniye dinlenme süresi bekleniyor...")
            except: pass
            await asyncio.sleep(loop_interval)

        except asyncio.CancelledError:
            break
        except Exception as e:
            log.exception("Döngü kritik hata: %s", e)
            await asyncio.sleep(15) # Hata olsa bile bot çökmez, otomatik kaldığı yerden devam eder
            
    tasks.pop(task_key, None)

async def stop_sender(uid, chat_id, context):
    db.set_user(uid, is_running=0)
    task_key = f"{uid}_auto_all_accounts"
    task = tasks.get(task_key)
    if task and not task.done():
        task.cancel()
        tasks.pop(task_key, None)
    await context.bot.send_message(chat_id=chat_id, text="⏹ **Reklam otomasyonu durduruldu.**")

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
            "Paketlerimizden birini seçerek hemen sınırsız güce sahip olun:\n\n"
            "📅 **Günlük Paket:** 200 TL\n"
            "📆 **Haftalık Paket:** 400 TL\n"
            "🗓 **Aylık Paket:** 1000 TL\n\n"
            "Satın almak istediğiniz pakete aşağıdaki butonlardan tıklayın:"
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
        if action == "buy_daily":
            p_name, p_price, days = "Günlük Paket", "200 TL", 1
        elif action == "buy_weekly":
            p_name, p_price, days = "Haftalık Paket", "400 TL", 7
        else:
            p_name, p_price, days = "Aylık Paket", "1000 TL", 30

        context.user_data["pending_buy_days"] = days
        context.user_data["pending_buy_name"] = p_name

        pay_text = (
            f"💳 **ÖDEME BİLDİRİMİ VE DETAYLARI**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📦 Seçilen Paket: **{p_name}** ({days} Gün)\n"
            f"💵 Ödenecek Tutar: **{p_price}**\n\n"
            f"🏦 **Banka Bilgileri:**\n"
            f"• **İBAN:** `TR100006200091000006969709`\n"
            f"• **Alıcı / İsim:** Garanti Ödeme ve Elektronik Para Hizmetleri A.Ş.\n"
            f"• **Açıklama Kodu:** `TAMİ7696919784875077`\n\n"
            f"⚠️ *Lütfen açıklamaya yukarıdaki kodu yazarak ödemeyi yapın. Ödemeyi yaptıktan sonra aşağıdaki butona basarak dekontunuzu gönderin.*"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📤 Dekont Gönder", callback_data="send_receipt_prompt")],
            [InlineKeyboardButton("⬅ Paketlere Dön", callback_data="pricing_panel")]
        ])
        await q.edit_message_text(pay_text, parse_mode="Markdown", reply_markup=kb)

    elif action == "send_receipt_prompt":
        context.user_data["flow"] = "awaiting_receipt"
        await q.edit_message_text("📤 Lütfen yaptığınız ödemenin **dekont fotoğrafını veya dosyasını** bota doğrudan gönderin (görsel olarak yükleyin):", reply_markup=sub_menu_keyboard())

    elif action == "add":
        if not has_valid_access(uid):
            await q.edit_message_text("❌ Aktif lisansınız yok. Lütfen önce abonelik satın alın.", reply_markup=sub_menu_keyboard())
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
            [InlineKeyboardButton("▶ Otomasyonu Başlat", callback_data="start_run"), InlineKeyboardButton("⏹ Otomasyonu Durdur", callback_data="stop_run")],
            [InlineKeyboardButton("🚫 Bu Hesaba Özel Grup Engelle", callback_data=f"acc_bl_{s_name}")],
            [InlineKeyboardButton("❌ Hesabı Sil", callback_data=f"del_{s_name}")],
            [InlineKeyboardButton("⬅ Geri", callback_data="accounts")]
        ])
        await q.edit_message_text("📱 **Hesap Yönetim Paneli**\nİstediğiniz işlemi seçin:", parse_mode="Markdown", reply_markup=kb)

    elif action.startswith("acc_bl_"):
        s_name = action.replace("acc_bl_", "")
        context.user_data["flow"] = f"acc_blacklist_add_{s_name}"
        await q.edit_message_text("🚫 Bu hesabın reklam göndermesini **istemediğiniz** grubun linkini veya kullanıcı adını gönderin:", parse_mode="Markdown", reply_markup=sub_menu_keyboard())

    elif action == "start_run":
        await start_sender(uid, q.message, context)

    elif action == "stop_run":
        await stop_sender(uid, q.message.chat_id, context)

    elif action.startswith("del_"):
        s_name = action.replace("del_", "")
        db.delete_session_db(s_name)
        clients.pop(s_name, None)
        await q.edit_message_text("✅ Hesap sistemden tamamen silindi.", reply_markup=get_keyboard(uid))

    elif action == "ad_menu":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📩 Kayıtlı Mesajlardan Al", callback_data="ad_saved")],
            [InlineKeyboardButton("✍️ Bot İçin Yeni Metin Yaz", callback_data="ad_custom")],
            [InlineKeyboardButton("⬅ Ana Menü", callback_data="home")]
        ])
        await q.edit_message_text("📩 **REKLAM İÇERİĞİ KAYNAĞI**\n\nReklamınızı botun nereden çekmesini istiyorsunuz?", parse_mode="Markdown", reply_markup=kb)

    elif action == "ad_saved":
        db.set_user(uid, custom_ad_text="")
        await q.edit_message_text("✅ Reklam kaynağı **Kayıtlı Mesajlar (Saved Messages)** olarak ayarlandı.", reply_markup=get_keyboard(uid))

    elif action == "ad_custom":
        context.user_data["flow"] = "set_custom_ad"
        await q.edit_message_text("✍️ Lütfen bota göndermesini istediğiniz **reklam metnini** (özel emojiler ve simgeler dahil) tek mesaj olarak gönderin:", parse_mode="Markdown", reply_markup=sub_menu_keyboard())

    elif action == "blacklist":
        context.user_data["flow"] = "blacklist"
        await q.edit_message_text("🚫 Genel engellenecek grup kullanıcı adını veya linkini gönderin:", parse_mode="Markdown", reply_markup=sub_menu_keyboard())

    elif action == "time_group":
        context.user_data["flow"] = "interval_group"
        await q.edit_message_text("⏱ İki grup gönderimi arasında kaç saniye beklensin? (Saniye girin, örn: 3):", parse_mode="Markdown", reply_markup=sub_menu_keyboard())

    elif action == "time_loop":
        context.user_data["flow"] = "interval_loop"
        await q.edit_message_text("⏳ Tüm gruplar bittikten sonra yeni tur için kaç saniye dinlenilsin? (Saniye girin, örn: 240):", parse_mode="Markdown", reply_markup=sub_menu_keyboard())

    elif action == "redeem":
        context.user_data["flow"] = "redeem"
        await q.edit_message_text("🔑 Lütfen sahip olduğunuz KEY kodunu gönderin:", parse_mode="Markdown", reply_markup=sub_menu_keyboard())

    elif action == "admin" and is_admin(uid):
        await q.edit_message_text("👑 **YÖNETİCİ REKLAM PANELİ**", parse_mode="Markdown", reply_markup=admin_keyboard())

    elif action == "key_create" and is_admin(uid):
        context.user_data["flow"] = "key_days"
        await q.edit_message_text("Kaç günlük KEY üretilsin? (Sadece gün sayısı girin, örn: 30):", parse_mode="Markdown", reply_markup=admin_keyboard())

    elif action == "key_list" and is_admin(uid):
        rows = db.list_licenses()
        txt = "📋 **ÜRETİLEN KEY LİSTESİ**\n\n"
        txt += "\n".join(f"`{r['code']}` | {r['duration_days']} Gün | Kullanıldı: {'Evet ❌' if r['is_used'] else 'Hayır ✅'}" for r in rows) if rows else "Henüz KEY yok."
        await q.edit_message_text(txt[:3900], parse_mode="Markdown", reply_markup=admin_keyboard())

# --- METİN VE DEKONT FOTOĞRAF İŞLEYİCİSİ ---
async def text_handler(update, context):
    uid, text = update.effective_user.id, update.effective_message.text.strip()
    flow = context.user_data.get("flow")
    
    # Dekont Fotoğrafı Gönderme Kontrolü
    if update.effective_message.photo and flow == "awaiting_receipt":
        photo = update.effective_message.photo[-1]
        days = context.user_data.get("pending_buy_days", 1)
        p_name = context.user_data.get("pending_buy_name", "Özel Paket")
        
        # Otomatik KEY Üretip Kullanıcıya Tanımlama
        code = "AUTO-" + "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))
        db.create_license(code, days)
        db.use_license(uid, code)
        
        context.user_data.clear()
        await update.effective_message.reply_text(
            f"✅ **Dekontunuz başarıyla alındı ve yönetmene iletildi!**\n"
            f"🎉 Otomatik olarak hesabınıza **{p_name} ({days} Gün)** tanımlandı.\n"
            f"Artık botu kesintisiz kullanabilirsiniz!",
            reply_markup=get_keyboard(uid)
        )
        
        # Admene Bildirim Gitmesi
        try:
            await context.bot.send_photo(
                chat_id=ADMIN_ID,
                photo=photo.file_id,
                caption=f"🚨 **YENİ ÖDEME / DEKONT BİLDİRİMİ!**\n\n👤 Kullanıcı ID: `{uid}`\n📦 Paket: {p_name} ({days} Gün)\n✅ Otomatik KEY tanımlandı ve süre eklendi."
            )
        except Exception as e:
            log.error(f"Admene bildirim atılamadı: {e}")
        return

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
            await update.effective_message.reply_text("📲 Telegram'dan gelen doğrulama kodunu gönderin:")
        except Exception as e:
            await client.disconnect()
            await update.effective_message.reply_text(f"❌ Kod gönderilemedi: {e}")

    elif flow == "code":
        item = pending.get(uid)
        if not item:
            context.user_data.clear()
            await update.effective_message.reply_text("Süre aşımı. Baştan başlayın.")
            return
        try:
            await item["client"].sign_in(phone=item["phone"], code=text, phone_code_hash=item["hash"])
            s_name = item["session_name"]
            s_string = item["client"].session.save()
            clients[s_name] = item["client"]
            db.save_session_db(uid, s_name, item["phone"], s_string)
            pending.pop(uid, None)
            context.user_data.clear()
            await update.effective_message.reply_text("✅ Hesap başarıyla bağlandı ve kaydedildi!", reply_markup=get_keyboard(uid))
        except SessionPasswordNeededError:
            context.user_data["flow"] = "2fa"
            await update.effective_message.reply_text("🔒 2FA şifrenizi girin:")
        except Exception as e:
            await update.effective_message.reply_text(f"❌ Giriş hatası: {e}")

    elif flow == "2fa":
        item = pending.get(uid)
        if not item:
            context.user_data.clear()
            await update.effective_message.reply_text("Süre aşımı.")
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

    elif flow.startswith("acc_blacklist_add_"):
        s_name = flow.replace("acc_blacklist_add_", "")
        target = text.replace("https://t.me/", "").replace("t.me/", "").strip().lstrip("@").lower()
        db.add_account_blacklist_db(s_name, target)
        context.user_data.clear()
        await update.effective_message.reply_text(f"✅ Bu hesap için gruba engel konuldu: {target}", reply_markup=get_keyboard(uid))

    elif flow == "set_custom_ad":
        db.set_user(uid, custom_ad_text=text)
        context.user_data.clear()
        await update.effective_message.reply_text("✅ Özel reklam metniniz kaydedildi! Artık bot bu metni gönderecek.", reply_markup=get_keyboard(uid))

    elif flow == "blacklist":
        target = "@" + text.replace("https://t.me/", "").replace("t.me/", "").strip().lstrip("@").lower()
        if uid not in blacklisted_groups: blacklisted_groups[uid] = set()
        blacklisted_groups[uid].add(target)
        context.user_data.clear()
        await update.effective_message.reply_text(f"✅ Genel engellenenlere eklendi: {target}", reply_markup=get_keyboard(uid))

    elif flow in ["interval_group", "interval_loop"]:
        try:
            val = int(text)
            db.set_user(uid, **({ "interval_seconds": val } if flow == "interval_group" else { "loop_interval_seconds": val }))
            context.user_data.clear()
            await update.effective_message.reply_text(f"✅ Süre güncellendi: {val} saniye", reply_markup=get_keyboard(uid))
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
            await update.effective_message.reply_text(f"✅ **KEY Üretildi:**\n\n`{code}`\nSüre: {days} Gün", parse_mode="Markdown", reply_markup=admin_keyboard())
        except ValueError:
            await update.effective_message.reply_text("Gün sayısı girin.")

def main():
    db.init_db()
    t = Thread(target=run_web)
    t.daemon = True
    t.start()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    app.add_handler(MessageHandler(filters.PHOTO, text_handler)) # Dekont fotoğrafları için eklendi
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
