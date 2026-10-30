import os
import time
import random
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, CallbackQueryHandler, MessageHandler, filters

ADMIN_USERNAME = "farketmezda"

# Aktif lisans keyleri ve süreleri (Saniye cinsinden)
active_keys = {
    "DP6W1TR5MS5": 345600, 
    "HARUN-1AY": 2592000,
    "HARUN-2AY": 5184000
}

user_licenses = {} 
user_sessions = {}

def get_user_session(user_id):
    if user_id not in user_sessions:
        user_sessions[user_id] = {
            "license_key": "Yok",
            "slot_limit": 3, # Her key en fazla 3 numara ekleyebilir
            "accounts": [],  # Her hesabın kendi reklam metni ve durumu ayrı tutulur
            "interval": "10 saniye",
            "excluded_groups": []
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

# Ana Menü
async def send_main_menu(update_or_query, text_title="👋 **Oto Mesaj ve Otomasyon Paneline Hoş Geldiniz!**"):
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
            [InlineKeyboardButton("🚀 Reklam Botunu Başlat", callback_data="tekli_baslat"),
             InlineKeyboardButton("🤖 Sadece Oto DM Cevap", callback_data="oto_dm")],
            [InlineKeyboardButton("👥 Hesaplarım & Reklamlarım", callback_data="hesaplarim"),
             InlineKeyboardButton("🚫 Hariç Tutulan Gruplar", callback_data="haric_gruplar_menu")],
            [InlineKeyboardButton("🛡️ Lisans Bilgim", callback_data="lisans_bilgi")],
        ]
        
        if is_admin:
            keyboard.append([InlineKeyboardButton("👑 Sınırsız Key Üretim Paneli", callback_data="admin_panel")])
            
        keyboard.extend([
            [InlineKeyboardButton("💡 Tavsiye", callback_data="tavsiye"),
             InlineKeyboardButton("📞 İletişim", callback_data="iletisim")],
            [InlineKeyboardButton("❓ Sık Sorulan Sorular", callback_data="sss")],
            [InlineKeyboardButton("🏠 Ana Menü", callback_data="ana_menu")]
        ])
        text = f"{text_title}\n\n✅ **Sistem Aktif. Her hesap tamamen kendi reklamını bağımsız yürütür.**"

    reply_markup = InlineKeyboardMarkup(keyboard)

    if hasattr(update_or_query, "message") and update_or_query.message:
        await update_or_query.message.reply_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    else:
        await update_or_query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode="Markdown")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_main_menu(update)

# Buton ve Panel Yöneticisi
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
                [InlineKeyboardButton("➕ 2 Aylık Key Üret", callback_data="gen_2ay"),
                 InlineKeyboardButton("➕ Sınırsız / 1 Yıllık Key Üret", callback_data="gen_sinirsiz")],
                [InlineKeyboardButton("🔙 Geri Dön", callback_data="ana_menu")]
            ]
            await query.edit_message_text(
                text="👑 **Sınırsız Key Üretim Paneli (@farketmezda)**",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown"
            )
        else:
            await query.answer("❌ Bu alana sadece yönetici erişebilir!", show_alert=True)

    elif data.startswith("gen_"):
        if username and username.lower() == ADMIN_USERNAME.lower():
            rand_code = ''.join(random.choices("ABCDEFGHJKLMNPQRSTUVWXYZ23456789", k=6))
            if data == "gen_1gun":
                key_name = f"BKMZ-1GUN-{rand_code}"
                active_keys[key_name] = 86400
            elif data == "gen_1ay":
                key_name = f"BKMZ-1AY-{rand_code}"
                active_keys[key_name] = 2592000
            elif data == "gen_2ay":
                key_name = f"BKMZ-2AY-{rand_code}"
                active_keys[key_name] = 5184000
            else:
                key_name = f"BKMZ-PRO-{rand_code}"
                active_keys[key_name] = 31536000
                
            await query.edit_message_text(
                text=f"✅ **Key Başarıyla Üretildi!**\n\n🔑 Kod:\n`{key_name}`",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Yönetici Paneline Dön", callback_data="admin_panel")]])
            )

    elif data == "haric_gruplar_menu":
        ex_list = "\n".join([f"• {g}" for g in session["excluded_groups"]]) if session["excluded_groups"] else "• Hariç tutulan grup yok."
        keyboard = [
            [InlineKeyboardButton("🗑️ Listeyi Temizle", callback_data="grup_temizle_haric")],
            [InlineKeyboardButton("🔙 Geri Dön", callback_data="ana_menu")]
        ]
        await query.edit_message_text(
            text=f"🚫 **Mesaj Gitmeyecek Hariç Tutulan Gruplar**\n\n{ex_list}\n\nİstemediğiniz grubun adını bota yazarak bu listeye ekleyebilirsiniz.",
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
                f"💻 **Aktif mesaj gönderen sayısı:** {len(user_sessions)}\n\n"
                f"🔒 **Lisans Bilgilerin**\n\n"
                f"👤 **Müşteri Adı:** {user.first_name} (Yönetici)\n"
                f"🔑 **Lisans Anahtarı:** SINIRSIZ-ADMIN\n"
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
                f"⏳ **Kalan Süre:** {kalan_str}\n"
                f"👥 **Kullanıcı Limiti (Slot):** {session['slot_limit']} Numara\n"
                f"⚙️ **Durum:** {durum_str}\n\n"
                f"✉️ Lisans almak veya süreni uzatmak için **@farketmezda** ile iletişime geçebilirsin."
            )

        keyboard = [[InlineKeyboardButton("🔙 Geri Dön", callback_data="ana_menu")]]
        await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data == "hesaplarim":
        acc_text = ""
        if session["accounts"]:
            for idx, acc in enumerate(session["accounts"], 1):
                st = "🟢 Çalışıyor" if acc["status"] else "🔴 Durduruldu"
                acc_text += f"{idx}) 🆔 `{acc['id']}`\n📌 Reklamı: *{acc['ad_message']}*\n{st} | 📞 `{acc['phone']}`\n\n"
        else:
            acc_text = "• Henüz eklenmiş hesap yok.\n\n"

        keyboard = [
            [InlineKeyboardButton("➕ Hesap Ekle", callback_data="hesap_ekle"),
             InlineKeyboardButton("🗑️ Hesap Sil", callback_data="hesap_sil")],
            [InlineKeyboardButton("▶️ Başlat", callback_data="hesap_baslat"),
             InlineKeyboardButton("🔴 Durdur", callback_data="durdur_islem")],
            [InlineKeyboardButton("🏠 Ana Menü", callback_data="ana_menu")]
        ]
        await query.edit_message_text(
            text=f"👥 **Bağlı Hesaplarınız ve Size Özel Reklamlarınız**\n\n"
                 f"📦 Slot: {len(session['accounts'])} / {session['slot_limit']}\n\n"
                 f"{acc_text}"
                 f"💡 Her hesap kendi özel reklam metniyle gruplara otomatik gönderim yapar.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )

    elif data == "hesap_ekle":
        if len(session["accounts"]) < session["slot_limit"]:
            # Her yeni hesaba varsayılan bir reklam metni atanır, kullanıcı bunu değiştirebilir
            new_acc = {
                "id": str(random.randint(100000, 999999)), 
                "phone": f"905{random.randint(100000000,999999999)}", 
                "status": False,
                "ad_message": "Henüz reklam metni girilmedi (Bota yazarak değiştirebilirsiniz)."
            }
            session["accounts"].append(new_acc)
            await query.answer("Yeni hesap slotu eklendi!", show_alert=True)
        else:
            await query.answer("⚠️ Max 3 numara ekleyebilirsiniz!", show_alert=True)
        await button_handler(update, context)

    elif data == "hesap_sil":
        if session["accounts"]:
            session["accounts"].pop()
            await query.answer("Hesap silindi.", show_alert=True)
        else:
            await query.answer("Silinecek hesap yok.", show_alert=True)
        await button_handler(update, context)

    elif data == "hesap_baslat":
        if session["accounts"]:
            session["accounts"][0]["status"] = True
            await query.answer("Bu hesaba ait reklam botu başlatıldı!", show_alert=True)
        else:
            await query.answer("Önce hesap ekleyin!", show_alert=True)
        await button_handler(update, context)

    elif data == "durdur_islem":
        if session["accounts"]:
            session["accounts"][0]["status"] = False
            await query.answer("Hesap durduruldu.", show_alert=True)
        await button_handler(update, context)

    elif data == "tekli_baslat":
        active_ads = "\n".join([f"• Hesap {idx}: *{acc['ad_message']}* ({'Çalışıyor' if acc['status'] else 'Durduruldu'})" for idx, acc in enumerate(session["accounts"], 1)]) if session["accounts"] else "• Aktif hesap yok."
        await query.edit_message_text(
            text=f"🚀 **Bağımsız Reklam Gönderim Paneli**\n\n"
                 f"Sisteme tanımlı hesaplarınızın reklam durumları:\n{active_ads}\n\n"
                 f"✏️ Yeni bir reklam metni kaydetmek için doğrudan bu sohbete mesajınızı yazabilirsiniz.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Geri Dön", callback_data="ana_menu")]])
        )
    elif data == "oto_dm":
        await query.edit_message_text(text="🤖 **Oto DM Cevap**\n\nAktif.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Geri Dön", callback_data="ana_menu")]]))
    elif data == "tavsiye":
        await query.edit_message_text(text="💡 Tavsiye: Her hesabın reklam metnini ayrı ayrı belirleyebilirsiniz.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Geri Dön", callback_data="ana_menu")]]))
    elif data == "iletisim":
        await query.edit_message_text(text="📞 **İletişim ve Lisans:**\n\n@farketmezda", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Geri Dön", callback_data="ana_menu")]]))
    elif data == "sss":
        await query.edit_message_text(text="❓ SSS: Herkes kendi reklamını yapar, diğer hesapları etkilemez.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Geri Dön", callback_data="ana_menu")]]))

# Mesaj Yakalama: Her kullanıcının kendi hesabına özel reklam metni ataması
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    username = update.message.from_user.username
    text = update.message.text.strip()
    
    if text in active_keys:
        duration = active_keys[text]
        user_licenses[user_id] = time.time() + duration
        
        session = get_user_session(user_id)
        session["license_key"] = text
        session["slot_limit"] = 3
        
        del active_keys[text]
        
        await update.message.reply_text("🎉 **Lisans Aktifleştirildi!** 3 numara slotu tanımlandı. /start yazabilirsiniz.", parse_mode="Markdown")
        return

    if not is_licensed(user_id, username):
        await update.message.reply_text("❌ Lisansınız yok. Lisans için **@farketmezda** ile iletişime geçin.")
        return

    session = get_user_session(user_id)
    
    # Kullanıcının ekli hesabı varsa, yazdığı metni o hesaba özel reklam metni yapalım
    if session["accounts"]:
        session["accounts"][0]["ad_message"] = text
        await update.message.reply_text(
            f"✅ **Bu Hesaba Özel Reklam Metni Güncellendi!**\n\n"
            f"📝 Metin: `{text}`\n\n"
            f"💡 Bu reklam sadece sizin hesaplarınız üzerinden kanallar hariç gruplara gönderilecektir. Diğer kullanıcıların reklamlarıyla asla karışmaz.",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text("⚠️ Önce 'Hesaplarım' menüsünden bir hesap ekleyin, ardından reklam metninizi yazın.")

if __name__ == '__main__':
    token = os.getenv("BOT_TOKEN")
    app = ApplicationBuilder().token(token).read_timeout(30).write_timeout(30).connect_timeout(30).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    
    print("İzole Edilmiş Çoklu Hesap Reklam Botu Aktif!")
    app.run_polling()
