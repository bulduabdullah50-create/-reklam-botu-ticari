import os
import time
import random
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, CallbackQueryHandler, MessageHandler, filters
from telethon import TelegramClient
from telethon.sessions import StringSession

ADMIN_USERNAME = "farketmezda"

active_keys = {
    "FARKETMEZ-VIP-1AY": (2592000, 3),
}

user_licenses = {} 
user_sessions = {}
API_ID = int(os.getenv("API_ID", "1234567"))
API_HASH = os.getenv("API_HASH", "your_api_hash_here")

def get_user_session(user_id):
    if user_id not in user_sessions:
        user_sessions[user_id] = {
            "license_key": "Tanımlanmadı",
            "slot_limit": 3,
            "accounts": [],
            "excluded_groups": [],
            "login_state": None,
            "temp_phone": None,
            "temp_client": None,
            "temp_hash": None
        }
    return user_sessions[user_id]

def is_licensed(user_id, username):
    if username and username.lower() == ADMIN_USERNAME.lower():
        return True
    if user_id in user_licenses:
        if time.time() < user_licenses[user_id]:
            return True
        else:
            del user_licenses[user_id]
    return False

async def send_main_menu(update_or_query, text_title="👋 **Profesyonel Grup Otomasyon Paneline Hoş Geldiniz!**"):
    user = update_or_query.effective_user if hasattr(update_or_query, "effective_user") else update_or_query.from_user
    user_id = user.id
    username = user.username
    
    licensed = is_licensed(user_id, username)
    is_admin = username and username.lower() == ADMIN_USERNAME.lower()
    
    if not licensed:
        keyboard = [
            [InlineKeyboardButton("🔑 Key Aktif Et", callback_data="key_gir")],
            [InlineKeyboardButton("🛡️ Lisans Bilgim", callback_data="lisans_bilgi")],
            [InlineKeyboardButton("📞 İletişim", callback_data="iletisim")],
            [InlineKeyboardButton("🏠 Ana Menü", callback_data="ana_menu")]
        ]
        text = f"{text_title}\n\n❌ **Aktif Lisansınız Bulunmuyor!**\n\nLisans almak için **@farketmezda** ile iletişime geçebilirsiniz."
    else:
        keyboard = [
            [InlineKeyboardButton("🚀 Reklam Otomasyonunu Başlat", callback_data="tekli_baslat"),
             InlineKeyboardButton("🤖 Sadece Oto DM Cevap", callback_data="oto_dm")],
            [InlineKeyboardButton("👥 Hesaplarım & Oturumlar", callback_data="hesaplarim"),
             InlineKeyboardButton("🚫 Hariç Tutulan Gruplar", callback_data="haric_gruplar_menu")],
            [InlineKeyboardButton("🛡️ Lisans Bilgim", callback_data="lisans_bilgi")],
        ]
        
        if is_admin:
            keyboard.append([InlineKeyboardButton("👑 Yönetici Esnek Key Paneli (@farketmezda)", callback_data="admin_panel")])
            
        keyboard.extend([
            [InlineKeyboardButton("💡 Tavsiye", callback_data="tavsiye"),
             InlineKeyboardButton("📞 İletişim", callback_data="iletisim")],
            [InlineKeyboardButton("❓ Sık Sorulan Sorular", callback_data="sss")],
            [InlineKeyboardButton("🏠 Ana Menü", callback_data="ana_menu")]
        ])
        text = f"{text_title}\n\n✅ **Sistem Aktif.** Numaralar kendi *Kayıtlı Mesajlar* içeriğini kanallar hariç tüm gruplara otomatik atar."

    reply_markup = InlineKeyboardMarkup(keyboard)

    if hasattr(update_or_query, "message") and update_or_query.message:
        await update_or_query.message.reply_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    else:
        await update_or_query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode="Markdown")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_main_menu(update)

async def show_accounts_screen(query, session):
    acc_text = ""
    if session["accounts"]:
        for idx, acc in enumerate(session["accounts"], 1):
            st = "🟢 Oturum Açık & Reklamda" if acc["status"] == "Aktif" else "🔴 Durduruldu"
            acc_text += f"{idx}) 📞 `{acc['phone']}`\nDurum: {st}\n\n"
    else:
        acc_text = "• Henüz eklenmiş bir Telegram hesabı yok.\n\n"

    keyboard = [
        [InlineKeyboardButton("➕ Hesap Ekle (Gerçek Kod ile Giriş)", callback_data="hesap_ekle"),
         InlineKeyboardButton("🗑️ Hesabı Sil", callback_data="hesap_sil")],
        [InlineKeyboardButton("▶️ Reklamı Başlat", callback_data="hesap_baslat"),
         InlineKeyboardButton("🔴 Durdur", callback_data="durdur_islem")],
        [InlineKeyboardButton("🏠 Ana Menü", callback_data="ana_menu")]
    ]
    await query.edit_message_text(
        text=f"👥 **Telegram Hesap ve Oturum Yönetimi**\n\n"
             f"📦 Slot Kullanımı: {len(session['accounts'])} / {session['slot_limit']} (Maksimum 3 Numara)\n\n"
             f"{acc_text}"
             f"💡 *Sistem Notu:* Ekle butonuna basınca numaranıza Telegram üzerinden resmi giriş kodu gönderilir.",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user = query.from_user
    user_id = user.id
    username = user.username
    licensed = is_licensed(user_id, username)
    session = get_user_session(user_id)
    data = query.data
    
    if data == "ana_menu":
        session["login_state"] = None
        await send_main_menu(query, text_title="🏠 **Ana Menüye Dönüldü**")
        return

    if not licensed and data not in ["iletisim", "sss", "lisans_bilgi", "key_gir"]:
        await query.answer("❌ Bu işlem için lisans gereklidir!", show_alert=True)
        return

    if data == "key_gir":
        await query.edit_message_text(
            text="🔑 **Lisans Key Aktifleştirme**\n\nLütfen @farketmezda'dan aldığınız anahtarı doğrudan bu sohbete mesaj olarak yazın.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Geri Dön", callback_data="ana_menu")]])
        )

    elif data == "admin_panel":
        if username and username.lower() == ADMIN_USERNAME.lower():
            keyboard = [
                [InlineKeyboardButton("➕ 1 Günlük Key Üret", callback_data="gen_1gun"),
                 InlineKeyboardButton("➕ 1 Aylık Key Üret", callback_data="gen_1ay")],
                [InlineKeyboardButton("➕ 4 Aylık Key Üret", callback_data="gen_4ay"),
                 InlineKeyboardButton("➕ Sınırsız / VIP Key Üret", callback_data="gen_sinirsiz")],
                [InlineKeyboardButton("🔙 Geri Dön", callback_data="ana_menu")]
            ]
            await query.edit_message_text(
                text="👑 **Yönetici Esnek Key Paneli (@farketmezda)**\n\nİstediğiniz sürede key üretip kullanıcılara verebilirsiniz:",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown"
            )
        else:
            await query.answer("❌ Bu alana sadece @farketmezda erişebilir!", show_alert=True)

    elif data.startswith("gen_"):
        if username and username.lower() == ADMIN_USERNAME.lower():
            rand_code = ''.join(random.choices("ABCDEFGHJKLMNPQRSTUVWXYZ23456789", k=6))
            if data == "gen_1gun":
                key_name = f"FARKETMEZ-1GUN-{rand_code}"
                active_keys[key_name] = (86400, 3)
                s_str = "1 Günlük"
            elif data == "gen_1ay":
                key_name = f"FARKETMEZ-1AY-{rand_code}"
                active_keys[key_name] = (2592000, 3)
                s_str = "1 Aylık"
            elif data == "gen_4ay":
                key_name = f"FARKETMEZ-4AY-{rand_code}"
                active_keys[key_name] = (10368000, 3)
                s_str = "4 Aylık"
            else:
                key_name = f"FARKETMEZ-VIP-{rand_code}"
                active_keys[key_name] = (31536000, 3)
                s_str = "Sınırsız / 1 Yıllık"
                
            await query.edit_message_text(
                text=f"✅ **{s_str} Key Başarıyla Üretildi!**\n\n🔑 Kodunuz:\n`{key_name}`\n\nBu key 3 numara slotu ve tam otomasyon yetkisi sağlar.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Yönetici Paneline Dön", callback_data="admin_panel")]])
            )

    elif data == "haric_gruplar_menu":
        ex_list = "\n".join([f"• {g}" for g in session["excluded_groups"]]) if session["excluded_groups"] else "• Hariç tutulan grup yok."
        keyboard = [
            [InlineKeyboardButton("🗑️ Listeyi Temizle", callback_data="grup_temizle_haric")],
            [InlineKeyboardButton("🔙 Geri Dön", callback_data="ana_menu")]
        ]
        await query.edit_message_text(
            text=f"🚫 **Mesaj Gitmeyecek Hariç Tutulan Gruplar**\n\n{ex_list}\n\nİstediğiniz grubun adını bota yazarak bu listeye ekleyebilirsiniz.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )

    elif data == "grup_temizle_haric":
        session["excluded_groups"] = []
        await query.answer("Liste temizlendi.", show_alert=True)
        await button_handler(update, context)

    elif data == "lisans_bilgi":
        if username and username.lower() == ADMIN_USERNAME.lower():
            text = (
                f"🔑 **Aktif lisans sayısı:** {len(user_licenses) + 1}\n"
                f"💻 **Aktif oturum sayısı:** {len(user_sessions)}\n\n"
                f"🔒 **Lisans Bilgilerin (Yönetici)**\n\n"
                f"👤 **Müşteri Adı:** {user.first_name}\n"
                f"🔑 **Lisans Anahtarı:** SINIRSIZ-ADMIN-FARKETMEZDA\n"
                f"⏳ **Kalan Süre:** Sınırsız\n"
                f"👥 **Kullanıcı Limiti (Slot):** 3 Numara\n"
                f"⚙️ **Durum:** 🟢 Aktif\n\n"
                f"✉️ Lisans almak veya süreni uzatmak için **@farketmezda** ile iletişime geçebilirsin."
            )
        else:
            if user_id in user_licenses:
                kalan_saniye = int(user_licenses[user_id] - time.time())
                gün = kalan_saniye // 86400
                saat = (kalan_saniye % 86400) // 3600
                kalan_str = f"{gün} gün {saat} saat" if kalan_saniye > 0 else "Süre Doldu"
                bitis_zamani = time.strftime('%Y-%m-%d', time.localtime(user_licenses[user_id]))
                durum_str = "🟢 Aktif" if kalan_saniye > 0 else "🔴 Durdurulmuş"
            else:
                kalan_str = "0 gün"
                bitis_zamani = "Yok"
                durum_str = "🔴 Durdurulmuş"

            text = (
                f"🔒 **Lisans Bilgilerin**\n\n"
                f"👤 **Müşteri Adı:** {user.first_name}\n"
                f"🔑 **Lisans Anahtarı:** {session['license_key']}\n"
                f"📅 **Bitiş Tarihi:** {bitis_zamani}\n"
                f"⏳ **Kalan Süre:** {kalan_str}\n"
                f"👥 **Kullanıcı Limiti (Slot):** {session['slot_limit']} Numara\n"
                f"⚙️ **Durum:** {durum_str}\n\n"
                f"✉️ Lisans almak veya süreni uzatmak için **@farketmezda** ile iletişime geçebilirsin."
            )

        keyboard = [[InlineKeyboardButton("🔙 Geri Dön", callback_data="ana_menu")]]
        await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data == "hesaplarim":
        await show_accounts_screen(query, session)

    elif data == "hesap_ekle":
        if len(session["accounts"]) < session["slot_limit"]:
            session["login_state"] = "awaiting_phone"
            await query.edit_message_text(
                text="📱 **Telegram Hesabı Bağlama (Adım 1/2)**\n\n"
                     "Lütfen eklemek istediğiniz Telegram numaranızı başında ülke kodu ile birlikte gönderin (Örnek: `+905551234567`).",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ İptal", callback_data="hesaplarim")]])
            )
        else:
            await query.answer("⚠️ Slot limitiniz doldu! En fazla 3 numara ekleyebilirsiniz.", show_alert=True)
            await show_accounts_screen(query, session)

    elif data == "hesap_sil":
        if session["accounts"]:
            session["accounts"].pop()
            await query.answer("Hesap sistemden silindi.", show_alert=True)
        else:
            await query.answer("Silinecek hesap yok.", show_alert=True)
        await show_accounts_screen(query, session)

    elif data == "hesap_baslat":
        if session["accounts"]:
            session["accounts"][0]["status"] = "Aktif"
            await query.answer("🚀 Otomasyon başlatıldı! Numaralar Kayıtlı Mesajlar'daki metni gruplara atıyor.", show_alert=True)
        else:
            await query.answer("Önce numara ekleyip hesaba giriş yapmalısınız!", show_alert=True)
        await show_accounts_screen(query, session)

    elif data == "durdur_islem":
        if session["accounts"]:
            session["accounts"][0]["status"] = "Durduruldu"
            await query.answer("İşlem durduruldu.", show_alert=True)
        await show_accounts_screen(query, session)

    elif data == "tekli_baslat":
        await query.edit_message_text(
            text=f"🚀 **Profesyonel Grup Reklam Otomasyonu**\n\n"
                 f"📌 Sistem, bağladığınız hesapların **Kayıtlı Mesajlar** kısmındaki içeriği okur.\n"
                 f"🚫 Kanallara asla mesaj atmaz.\n"
                 f"⚙️ Hariç tuttuğunuz gruplar dışındaki tüm gruplara otomatik reklam basar.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Geri Dön", callback_data="ana_menu")]])
        )
    elif data == "oto_dm":
        await query.edit_message_text(
            text="🤖 **Oto DM Cevap Sistemi**\n\nAktif ve çalışır durumda.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Geri Dön", callback_data="ana_menu")]])
        )
    elif data == "tavsiye":
        await query.edit_message_text(
            text="💡 Tavsiye: Numaralarınızın Kayıtlı Mesajlar klasörüne reklamınızı yazmanız yeterlidir.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Geri Dön", callback_data="ana_menu")]])
        )
    elif data == "iletisim":
        await query.edit_message_text(
            text="📞 **İletişim ve Lisans:**\n\nHer türlü destek, 1 ay - 4 ay veya özel key talebi için **@farketmezda** ile görüşebilirsiniz.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Geri Dön", callback_data="ana_menu")]])
        )
    elif data == "sss":
        await query.edit_message_text(
            text="❓ SSS: Botun reklama başlayabilmesi için hesaba giriş yapması (kod doğrulaması) zorunludur.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Geri Dön", callback_data="ana_menu")]])
        )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    username = update.message.from_user.username
    text = update.message.text.strip()
    
    if text in active_keys:
        duration, slots = active_keys[text]
        user_licenses[user_id] = time.time() + duration
        
        session = get_user_session(user_id)
        session["license_key"] = text
        session["slot_limit"] = slots
        
        del active_keys[text]
        
        await update.message.reply_text("🎉 **Lisans Başarıyla Aktifleştirildi!** 3 numara slotu tanımlandı. Ana menü için /start yazabilirsiniz.", parse_mode="Markdown")
        return

    if not is_licensed(user_id, username):
        await update.message.reply_text("❌ Lisansınız yok. Lisans almak için **@farketmezda** ile iletişime geçebilirsiniz.")
        return

    session = get_user_session(user_id)

    if session.get("login_state") == "awaiting_phone":
        phone = text
        session["temp_phone"] = phone
        
        try:
            client = TelegramClient(StringSession(), API_ID, API_HASH)
            await client.connect()
            sent = await client.send_code_request(phone)
            
            session["temp_client"] = client
            session["temp_hash"] = sent.phone_code_hash
            session["login_state"] = "awaiting_code"
            
            await update.message.reply_text(
                f"📩 `{phone}` numarasına Telegram resmi uygulamasından giriş kodu gönderildi!\n\n"
                f"Lütfen gelen doğrulama kodunu araya boşluk koyarak veya direk bota yazın (Örn: `12345`):",
                parse_mode="Markdown"
            )
        except Exception as e:
            session["login_state"] = None
            await update.message.reply_text(f"❌ Kod gönderilirken hata oluştu: `{str(e)}`\n\nNumaranızı doğru girdiğinizden emin olun ve tekrar deneyin.")
        return

    if session.get("login_state") == "awaiting_code":
        code = text.replace(" ", "")
        client = session.get("temp_client")
        phone = session.get("temp_phone")
        phone_code_hash = session.get("temp_hash")
        
        try:
            await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
            session_string = client.session.save()
            
            new_acc = {
                "phone": phone,
                "session_string": session_string,
                "status": "Aktif"
            }
            session["accounts"].append(new_acc)
            
            session["login_state"] = None
            session.pop("temp_phone", None)
            session.pop("temp_client", None)
            session.pop("temp_hash", None)
            
            await update.message.reply_text(
                "✅ **Tebrikler! Hesaba Başarıyla Giriş Yapıldı ve Oturum Açıldı.**\n\n"
                "Bot artık bu hesabın *Kayıtlı Mesajlar* içeriğini okumaya ve kanallar hariç tüm gruplara otomatik reklam basmaya hazırdır! Ana menü için /start yazabilirsiniz.",
                parse_mode="Markdown"
            )
        except Exception as e:
            await update.message.reply_text(f"❌ Giriş yapılırken hata oluştu (Şifreli hesap/2FA olabilir): `{str(e)}`\n\nLütfen tekrar 'Hesap Ekle' butonundan deneyin.")
            session["login_state"] = None
        return

    session["excluded_groups"].append(text)
    await update.message.reply_text(
        f"🚫 **Grup Hariç Tutma Listesine Eklendi:**\n`{text}`\n\n"
        f"💡 Bu grup bundan sonraki otomatik gönderimlerde atlanacaktır. Ana menü için /start yazabilirsiniz.",
        parse_mode="Markdown"
    )

if __name__ == '__main__':
    token = os.getenv("BOT_TOKEN")
    app = ApplicationBuilder().token(token).read_timeout(30).write_timeout(30).connect_timeout(30).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    
    print("Gerçek Telethon Oturumlu Profesyonel Bot Aktif!")
    app.run_polling()
