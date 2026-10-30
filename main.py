import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, CallbackQueryHandler, MessageHandler, filters

# Bot başlatma ve ana menü komutu
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("📢 Reklam Ver / Yayınla", callback_data="reklam_ver")],
        [InlineKeyboardButton("ℹ️ Bilgi & Yardım", callback_data="bilgi")],
        [InlineKeyboardButton("📞 İletişim / Destek", callback_data="iletisim")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "👋 **Ticari Otomasyon Botuna Hoş Geldiniz!**\n\n"
        "Aşağıdaki menüden yapmak istediğiniz işlemi seçebilirsiniz.",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )

# Buton tıklamalarını yönetme
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "reklam_ver":
        await query.edit_message_text(
            text="📢 **Reklam Yayınlama Paneli**\n\n"
                 "Lütfen paylaşmak istediğiniz reklam metnini veya görselini doğrudan bu sohbete gönderin.",
            parse_mode="Markdown"
        )
    elif query.data == "bilgi":
        await query.edit_message_text(
            text="ℹ️ **Bilgilendirme**\n\n"
                 "Bu bot ticari ilan ve reklam paylaşımlarınızı hızlı ve düzenli bir şekilde yönetmeniz için kurulmuştur.",
            parse_mode="Markdown"
        )
    elif query.data == "iletisim":
        await query.edit_message_text(
            text="📞 **İletişim Bilgileri**\n\n"
                 "• Telefon: 0555 024 34 80\n"
                 "• Instagram: @harun.bkmz",
            parse_mode="Markdown"
        )

# Gelen mesajları yakalama (reklam veya metinler için)
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    await update.message.reply_text(f"✅ Mesajınız alındı ve işleme konuldu!\n\nAlınan metin: {text}")

if __name__ == '__main__':
    token = os.getenv("BOT_TOKEN")
    # Timeout ayarları eklenmiş, Render bağlantı kopmalarını önleyen yapı
    app = ApplicationBuilder().token(token).read_timeout(30).write_timeout(30).connect_timeout(30).build()
    
    # Komut ve buton yönlendiricileri
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    
    print("Ticari Bot Aktif!")
    app.run_polling()
