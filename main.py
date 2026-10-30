import os
import time
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, CallbackQueryHandler, MessageHandler, filters

# Ana Yönetici (Sadece bu hesap key üretebilir)
ADMIN_USERNAME = "farketmezda"

# Üretilen key'lerin ve kullanıcıların sürelerinin tutulduğu veritabanı simülasyonu
# Format: { "KEY123": süre_saniye }
active_keys = {
    "HARUN-1GUN": 86400,       # 1 Gün (Saniye cinsinden)
    "HARUN-1AY": 2592000,      # 30 Gün
    "HARUN-2AY": 5184000       # 60 Gün
}

# Kullanıcı oturumları ve bitiş süreleri: { user_id: bitis_zaman_damgasi }
user_licenses = {}
user_sessions = {}

def get_user_session(user_id):
    if user_id not in user_sessions:
        user_sessions[user_id] = {
            "active_bot": False,
            "connected_accounts": [],
            "saved_message": "Henüz mesaj kaydedilmedi.",
            "interval": "10 saniye"
        }
    return user_sessions[user_id]

# Lisans Kontrol Fonksiyonu
def is_licensed(user_id, username):
    if username and username.lower() == ADMIN_USERNAME.lower():
        return True
    
    # Süresi bitmiş mi kontrol et
    if user_id in user_licenses:
        if time.time() < user_licenses[user_id]:
            return True
        else:
            # Süresi dolmuşsa sil
            del user_licenses[user_id]
    return False

# Ana Menü
async def send_main_menu(update_or_query, text_title="👋 **Oto Mesaj ve Otomasyon Paneline Hoş Geldiniz!**"):
    user = update_or_query.effective_user if hasattr(update_or_query, "effective_user") else update_or_query.from_user
    user_id = user.id
    username = user.username
    
    licensed = is_licensed(user_id, username)
    is_admin = username and username.lower() == ADMIN_USERNAME.lower()
    
    if not licensed:
        keyboard = [
            [InlineKeyboardButton("🔑 Key (Lisans) Aktif Et", callback_data="key_gir")],
            [InlineKeyboardButton("🛡️ Lisans Bilgim", callback_data="lisans_bilgi")],
            [InlineKeyboardButton("📞 İletişim / Key Al", callback_data="iletisim")],
            [InlineKeyboardButton("🏠 Ana Menü", callback_data="ana_menu")]
        ]
        text = (
            f"{text_title}\n\n"
            "❌ **Aktif Lisansınız Bulunmuyor!**\n\n"
            "🔒 Botu kullanabilmek için @farketmezda adresinden temin edeceğiniz lisans anahtarını **'Key Aktif Et'** butonuna basarak girmeniz gerekmektedir."
        )
    else:
        keyboard = [
            [InlineKeyboardButton("🚀 Tekli Botu Başlat", callback_data="tekli_baslat"),
             InlineKeyboardButton("🤖 Sadece Oto DM Cevap", callback_data="oto_dm")],
            [InlineKeyboardButton("👥 Hesaplarım", callback_data="hesaplarim"),
             InlineKeyboardButton("🛡️ Lisans Bilgim", callback_data="lisans_bilgi")],
        ]
        if is_admin:
            keyboard.append([InlineKeyboardButton("👑 Yönetici Key Üretim Paneli", callback_data="admin_panel")])
            
        keyboard.extend([
            [InlineKeyboardButton("💡 Tavsiye", callback_data="tavsiye"),
             InlineKeyboardButton("📞 İletişim", callback_data="iletisim")],
            [InlineKeyboardButton("❓ Sık Sorulan Sorular", callback_data="sss")],
            [InlineKeyboardButton("💬 Destek", url="https://t.me/farketmezda")],
            [InlineKeyboardButton("🏠 Ana Menü", callback_data="ana_menu")]
        ])
        text = (
            f"{text_title}\n\n"
            "✅ **Lisansınız Onaylı ve Aktif!**\n\n"
            "💡 Aşağıdaki butonlardan işlemlerinizi yönetebilirsiniz."
        )

    reply_markup = InlineKeyboardMarkup(keyboard)

    if hasattr(update_or_query, "message") and update_or_query.message:
        await update_or_query.message.reply_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    else:
        await update_or_query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode="Markdown")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_main_menu(update)

# Buton ve İşlem Yönlendiricisi
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    username = query.from_user.username
    licensed = is_licensed(user_id, username)
    session = get_user_session(user_id)
    data = query.data
    
    if data == "ana_menu":
        await send_main_menu(query, text_title="🏠 **Ana Menüye Dönüldü**")
        return

    if not licensed and data not in ["iletisim", "sss", "lisans_bilgi", "key_gir"]:
        await query.answer("❌ Bu işlem için geçerli bir lisans anahtarı girmeniz gerekir!", show_alert=True)
        await send_main_menu(query, text_title="⚠️ **Kısıtlı Erişim**")
        return

    if data == "key_gir":
        await query.edit_message_text(
            text="🔑 **Lisans Key Aktifleştirme**\n\n"
                 "Lütfen @farketmezda'dan aldığınız lisans anahtarını doğrudan bu sohbete **mesaj olarak** gönderin.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Geri Dön", callback_data="ana_menu")]])
        )

    elif data == "admin_panel":
        if username and username.lower() == ADMIN_USERNAME.lower():
            keyboard = [
                [InlineKeyboardButton("➕ 1 Günlük Key Üret", callback_data="gen_1gun"),
                 InlineKeyboardButton("➕ 1 Aylık Key Üret", callback_data="gen_1ay")],
                [InlineKeyboardButton("➕ 2 Aylık Key Üret", callback_data="gen_2ay")],
                [InlineKeyboardButton("🔙 Geri Dön", callback_data="ana_menu")]
            ]
            await query.edit_message_text(
                text="👑 **Yönetici Key Üretim Paneli**\n\n"
                     "Aşağıdaki butonlara basarak anında yeni lisans anahtarları oluşturabilirsiniz:",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown"
            )
        else:
            await query.answer("Bu panele sadece @farketmezda erişebilir!", show_alert=True)

    elif data.startswith("gen_"):
        if username and username.lower() == ADMIN_USERNAME.lower():
            import random
            rand_code = ''.join(random.choices("ABCDEFGHJKLMNPQRSTUVWXYZ23456789", k=6))
            
            if data == "gen_1gun":
                key_name = f"BKMZ-1GUN-{rand_code}"
                active_keys[key_name] = 86400
                süre_str = "1 Günlük"
            elif data == "gen_1ay":
                key_name = f"BKMZ-1AY-{rand_code}"
                active_keys[key_name] = 2592000
                süre_str = "1 Aylık"
            elif data == "gen_2ay":
                key_name = f"BKMZ-2AY-{rand_code}"
                active_keys[key_name] = 5184000
                süre_str = "2 Aylık"
                
            await query.edit_message_text(
                text=f"✅ **Yeni {süre_str} Key Başarıyla Üretildi!**\n\n"
                     f"🔑 Anahtar Kodunuz:\n`{key_name}`\n\n"
                     f"Bu kodu kullanıcıya ileterek aktif etmesini sağlayabilirsiniz.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Yönetici Paneline Dön", callback_data="admin_panel")]]),
                parse_mode="Markdown"
            )

    elif data == "tekli_baslat":
        status_text = "🟢 Aktif (Çalışıyor)" if session["active_bot"] else "🔴 Durduruldu"
        keyboard = [
            [InlineKeyboardButton("▶️ Başlat / Durdur Değiştir", callback_data="toggle_bot")],
            [InlineKeyboardButton("⏱️ Gönderim Süresini Ayarla", callback_data="set_interval")],
            [InlineKeyboardButton("🔙 Geri Dön", callback_data="ana_menu")]
        ]
        await query.edit_message_text(
            text=f"🚀 **Tekli Bot Başlatma Paneli**\n\n"
                 f"• Durum: {status_text}\n"
                 f"• Süre Aralığı: {session['interval']}\n"
                 f"• Kayıtlı Mesaj:\n`{session['saved_message']}`",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        
    elif data == "toggle_bot":
        session["active_bot"] = not session["active_bot"]
        status_text = "🟢 Aktif Edildi!" if session["active_bot"] else "🔴 Durduruldu!"
        await query.answer(status_text, show_alert=True)
        
        curr_status = "🟢 Aktif (Çalışıyor)" if session["active_bot"] else "🔴 Durduruldu"
        keyboard = [
            [InlineKeyboardButton("▶️ Başlat / Durdur Değiştir", callback_data="toggle_bot")],
            [InlineKeyboardButton("⏱️ Gönderim Süresini Ayarla", callback_data="set_interval")],
            [InlineKeyboardButton("🔙 Geri Dön", callback_data="ana_menu")]
        ]
        await query.edit_message_text(
            text=f"🚀 **Tekli Bot Başlatma Paneli**\n\n"
                 f"• Durum: {curr_status}\n"
                 f"• Süre Aralığı: {session['interval']}\n"
                 f"• Kayıtlı Mesaj:\n`{session['saved_message']}`",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )

    elif data == "set_interval":
        keyboard = [
            [InlineKeyboardButton("5 Saniye", callback_data="int_5"), InlineKeyboardButton("10 Saniye", callback_data="int_10")],
            [InlineKeyboardButton("30 Saniye", callback_data="int_30"), InlineKeyboardButton("1 Dakika", callback_data="int_60")],
            [InlineKeyboardButton("🔙 Geri Dön", callback_data="tekli_baslat")]
        ]
        await query.edit_message_text(
            text="⏱️ **Gönderim Süresi Seçimi**\n\nSüre aralığını seçin:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )

    elif data.startswith("int_"):
        val = data.split("_")[1]
        session["interval"] = "1 Dakika" if val == "60" else f"{val} saniye"
        await query.answer(f"Süre {session['interval']} yapıldı!", show_alert=True)
        
        status_text = "🟢 Aktif (Çalışıyor)" if session["active_bot"] else "🔴 Durduruldu"
        keyboard = [
            [InlineKeyboardButton("▶️ Başlat / Durdur Değiştir", callback_data="toggle_bot")],
            [InlineKeyboardButton("⏱️ Gönderim Süresini Ayarla", callback_data="set_interval")],
            [InlineKeyboardButton("🔙 Geri Dön", callback_data="ana_menu")]
        ]
        await query.edit_message_text(
            text=f"🚀 **Tekli Bot Başlatma Paneli**\n\n"
                 f"• Durum: {status_text}\n"
                 f"• Süre Aralığı: {session['interval']}\n"
                 f"• Kayıtlı Mesaj:\n`{session['saved_message']}`",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        
    elif data == "oto_dm":
        await query.edit_message_text(
            text="🤖 **Oto DM Cevap Sistemi**\n\nAktif ve çalışır durumda.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Geri Dön", callback_data="ana_menu")]])
        )
        
    elif data == "hesaplarim":
        await query.edit_message_text(
            text="👥 **Bağlı Hesaplarım**\n\nOturumunuza bağlı hesap bulunmuyor.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Geri Dön", callback_data="ana_menu")]])
        )
        
    elif data == "lisans_bilgi":
        keyboard = [[InlineKeyboardButton("🔙 Geri Dön", callback_data="ana_menu")]]
        if username and username.lower() == ADMIN_USERNAME.lower():
            text = "🛡️ **Lisans Durumunuz:** Ana Yönetici (@farketmezda) - Sınırsız Yetki"
        else:
            if user_id in user_licenses:
                kalan_saniye = int(user_licenses[user_id] - time.time())
                gün = kalan_saniye // 86400
                s_text = f"Aktif ({gün} gün kaldı)" if gün > 0 else f"Aktif ({kalan_saniye // 3600} saat kaldı)"
            else:
                s_text = "Lisans Yok"
            text = f"🛡️ **Lisans Durumunuz:** {s_text}\n\nKey aktif etmek için ana menüden 'Key Aktif Et' butonunu kullanabilirsiniz."
        await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data == "tavsiye":
        await query.edit_message_text(
            text="💡 **Tavsiye:** Süre aralıklarını dengeli tutun.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Geri Dön", callback_data="ana_menu")]])
        )
        
    elif data == "iletisim":
        await query.edit_message_text(
            text="📞 **İletişim ve Key Satın Al:**\n\n• @farketmezda",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Geri Dön", callback_data="ana_menu")]])
        )
        
    elif data == "sss":
        await query.edit_message_text(
            text="❓ **SSS:** Key'leri @farketmezda üzerinden temin edebilirsiniz.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Geri Dön", callback_data="ana_menu")]])
        )

# Mesaj Yakalama & Key Kontrol Mekanizması
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    username = update.message.from_user.username
    text = update.message.text.strip()
    
    # Eğer kullanıcı bir key girdiyse kontrol et
    if text in active_keys:
        duration = active_keys[text]
        # Eğer kullanıcının zaten süresi varsa üstüne ekle, yoksa baştan başlat
        current_time = time.time()
        base_time = user_licenses[user_id] if user_id in user_licenses and user_licenses[user_id] > current_time else current_time
        
        user_licenses[user_id] = base_time + duration
        del active_keys[text] # Kullanılan key'i bir daha kullanılmasın diye sil
        
        await update.message.reply_text(
            "🎉 **Tebrikler! Lisans Anahtarı Başarıyla Aktifleştirildi.**\n\n"
            "Botun tüm özellikleri hesabınız için açıldı. Ana menüye dönmek için /start yazabilirsiniz.",
            parse_mode="Markdown"
        )
        return

    # Normal lisans kontrolü
    if not is_licensed(user_id, username):
        await update.message.reply_text("❌ Geçerli bir lisansınız yok. Lütfen @farketmezda hesabından 1 günlük, 1 aylık veya 2 aylık key alıp bota gönderin.")
        return

    # Mesaj kaydetme
    session = get_user_session(user_id)
    session["saved_message"] = text
    
    await update.message.reply_text(
        f"✅ **Mesajınız Kaydedildi!**\n\nİçerik: `{text}`\n\nAna menüye dönmek için /start yazabilirsiniz.",
        parse_mode="Markdown"
    )

if __name__ == '__main__':
    token = os.getenv("BOT_TOKEN")
    app = ApplicationBuilder().token(token).read_timeout(30).write_timeout(30).connect_timeout(30).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    
    print("Süreli Key Sistemli Bot Aktif!")
    app.run_polling()
