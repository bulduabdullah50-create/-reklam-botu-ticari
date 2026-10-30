
import os
import secrets
import logging

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

import database


logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN ortam değişkeni eksik.")

if not ADMIN_ID or not ADMIN_ID.isdigit():
    raise RuntimeError("ADMIN_ID ortam değişkeni eksik veya hatalı.")

ADMIN_ID = int(ADMIN_ID)

database.init_db()


def is_admin(user_id):
    return user_id == ADMIN_ID


def main_menu():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📢 Reklam Yönetimi", callback_data="ads"),
            InlineKeyboardButton("👥 Hesaplarım", callback_data="accounts"),
        ],
        [
            InlineKeyboardButton("🗂 İzinli Gruplar", callback_data="groups"),
            InlineKeyboardButton("🚫 Hariç Tutulanlar", callback_data="excluded"),
        ],
        [
            InlineKeyboardButton("⚙️ Ayarlar", callback_data="settings"),
            InlineKeyboardButton("💎 Lisans Bilgilerim", callback_data="license"),
        ],
        [
            InlineKeyboardButton("🔑 Lisans Anahtarı Kullan", callback_data="redeem"),
        ],
    ])


def admin_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔑 Key Oluştur", callback_data="create_key")],
        [InlineKeyboardButton("🏠 Ana Menü", callback_data="home")],
    ])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    text = (
        "✨ FARKETMEZ PANEL\n\n"
        "Profesyonel yönetim paneline hoş geldin.\n\n"
        "Devam etmek için aşağıdaki menüyü kullan."
    )

    await update.message.reply_text(
        text,
        reply_markup=main_menu(),
    )

    if is_admin(user.id):
        await update.message.reply_text(
            "👑 Yönetici paneli",
            reply_markup=admin_menu(),
        )


async def my_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"Telegram kullanıcı ID'n: `{update.effective_user.id}`",
        parse_mode="Markdown",
    )


async def button_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    action = query.data

    if action == "home":
        await query.edit_message_text(
            "✨ FARKETMEZ PANEL\n\nAna menü:",
            reply_markup=main_menu(),
        )
        return

    if action == "create_key":
        if not is_admin(user_id):
            await query.edit_message_text("⛔ Bu alan sadece yöneticiye açıktır.")
            return

        context.user_data["state"] = "key_duration"
        await query.edit_message_text(
            "🔑 Yeni lisans oluşturma\n\n"
            "Lisans süresini saat cinsinden gönder.\n"
            "Örnek: 720 = 30 gün"
        )
        return

    if action == "redeem":
        context.user_data["state"] = "redeem_key"
        await query.edit_message_text(
            "🔐 Lisans anahtarını gönder:"
        )
        return

    if action == "license":
        if not database.license_is_valid(user_id):
            await query.edit_message_text(
                "❌ Aktif lisans bulunamadı.",
                reply_markup=main_menu(),
            )
            return

        await query.edit_message_text(
            "💎 Lisansın aktif.\n\n"
            "Süre bilgisi lisans anahtarının kayıtlarından kontrol edilir.",
            reply_markup=main_menu(),
        )
        return

    if action == "settings":
        settings = database.get_settings(user_id)

        await query.edit_message_text(
            "⚙️ Reklam ayarları\n\n"
            f"Mesajlar arası bekleme: {settings['message_interval']} saniye\n"
            f"Tur sonrası bekleme: {settings['cycle_wait']} saniye\n\n"
            "Aralıkları değiştirmek için /aralik komutunu kullan."
        )
        return

    if action == "groups":
        groups = database.list_allowed_groups(user_id)

        if not groups:
            text = "Henüz izinli grup eklenmemiş."
        else:
            text = "🗂 İzinli grupların:\n\n" + "\n".join(
                f"• {group['chat_id']}" for group in groups
            )

        await query.edit_message_text(
            text + "\n\nYeni grup eklemek için /izinli_grup komutunu kullan.",
            reply_markup=main_menu(),
        )
        return

    if action == "excluded":
        groups = database.list_excluded_groups(user_id)

        text = (
            "🚫 Hariç tutulan gruplar:\n\n"
            + ("\n".join(groups) if groups else "Liste boş.")
        )

        await query.edit_message_text(text, reply_markup=main_menu())
        return

    if action == "accounts":
        await query.edit_message_text(
            "👥 Hesap yönetimi\n\n"
            "Çoklu hesap bağlantısı henüz etkin değil. "
            "Güvenli oturum kaydı tamamlandığında burada görünecek.",
            reply_markup=main_menu(),
        )
        return

    if action == "ads":
        await query.edit_message_text(
            "📢 Reklam yönetimi\n\n"
            "Otomatik gönderim henüz etkin değil.\n"
            "Yalnızca yönettiğin veya açık reklam izni aldığın gruplar "
            "için gönderim desteği hazırlanacaktır.",
            reply_markup=main_menu(),
        )
        return


async def text_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user_id = update.effective_user.id
    state = context.user_data.get("state")
    message = update.message.text.strip()

    if state == "key_duration":
        if not is_admin(user_id):
            context.user_data.clear()
            return

        try:
            hours = int(message)
            if hours < 1 or hours > 87600:
                raise ValueError
        except ValueError:
            await update.message.reply_text(
                "Geçerli bir saat değeri gir. En az 1, en fazla 87600."
            )
            return

        context.user_data["key_duration"] = hours
        context.user_data["state"] = "key_slots"

        await update.message.reply_text(
            "Bu lisans kaç hesapta kullanılabilsin?\n"
            "Örnek: 1"
        )
        return

    if state == "key_slots":
        if not is_admin(user_id):
            context.user_data.clear()
            return

        try:
            slots = int(message)
            if slots < 1 or slots > 20:
                raise ValueError
        except ValueError:
            await update.message.reply_text(
                "Hesap sayısı 1 ile 20 arasında olmalı."
            )
            return

        hours = context.user_data.get("key_duration")
        key = secrets.token_urlsafe(18)

        try:
            database.create_license(key, hours, slots)
        except Exception:
            logger.exception("Lisans oluşturulamadı.")
            await update.message.reply_text("Lisans oluşturulurken hata oluştu.")
            context.user_data.clear()
            return

        context.user_data.clear()

        await update.message.reply_text(
            "✅ Lisans oluşturuldu.\n\n"
            f"Süre: {hours} saat\n"
            f"Hesap limiti: {slots}\n\n"
            f"Anahtar: `{key}`",
            parse_mode="Markdown",
        )
        return

    if state == "redeem_key":
        success = database.activate_license(message, user_id)
        context.user_data.clear()

        if success:
            await update.message.reply_text(
                "✅ Lisans başarıyla etkinleştirildi.",
                reply_markup=main_menu(),
            )
        else:
            await update.message.reply_text(
                "❌ Anahtar geçersiz, süresi dolmuş veya kullanılmış.",
                reply_markup=main_menu(),
            )
        return

    if state == "interval":
        try:
            seconds = int(message)
            if seconds < 10 or seconds > 86400:
                raise ValueError
        except ValueError:
            await update.message.reply_text(
                "10 ile 86400 arasında saniye gir."
            )
            return

        settings = database.get_settings(user_id)
        database.save_settings(
            user_id,
            seconds,
            settings["cycle_wait"],
        )
        context.user_data.clear()

        await update.message.reply_text("✅ Mesaj aralığı kaydedildi.")
        return

    if state == "cycle_wait":
        try:
            seconds = int(message)
            if seconds < 60 or seconds > 604800:
                raise ValueError
        except ValueError:
            await update.message.reply_text(
                "60 ile 604800 arasında saniye gir."
            )
            return

        settings = database.get_settings(user_id)
        database.save_settings(
            user_id,
            settings["message_interval"],
            seconds,
        )
        context.user_data.clear()

        await update.message.reply_text("✅ Tur sonrası bekleme kaydedildi.")
        return

    if state == "group_id":
        if not database.license_is_valid(user_id):
            context.user_data.clear()
            await update.message.reply_text("Aktif lisansın bulunmuyor.")
            return

        chat_id = message
        if len(chat_id) > 100:
            await update.message.reply_text("Grup bilgisi çok uzun.")
            return

        context.user_data["group_id"] = chat_id
        context.user_data["state"] = "group_consent"

        await update.message.reply_text(
            "Bu grupta reklam paylaşmak için açık iznin var mı?\n\n"
            "İzin kaydını açıklayan kısa bir not gönder. "
            "Örnek: Grup yöneticisinden alınan reklam izni."
        )
        return

    if state == "group_consent":
        chat_id = context.user_data.get("group_id")

        if not chat_id:
            context.user_data.clear()
            await update.message.reply_text("İşlem iptal edildi.")
            return

        database.add_allowed_group(
            user_id,
            chat_id,
            consent_note=message[:500],
        )

        context.user_data.clear()
        await update.message.reply_text(
            "✅ Grup izinli listene eklendi."
        )
        return

    await update.message.reply_text(
        "Komutları kullanabilir veya menüden seçim yapabilirsin."
    )


async def set_interval(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not database.license_is_valid(update.effective_user.id):
        await update.message.reply_text("Aktif lisansın bulunmuyor.")
        return

    context.user_data["state"] = "interval"
    await update.message.reply_text(
        "Mesajlar arasında kaç saniye beklensin?\n"
        "10 - 86400"
    )


async def set_cycle_wait(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not database.license_is_valid(update.effective_user.id):
        await update.message.reply_text("Aktif lisansın bulunmuyor.")
        return

    context.user_data["state"] = "cycle_wait"
    await update.message.reply_text(
        "Tüm izinli gruplar tamamlandıktan sonra kaç saniye beklensin?\n"
        "60 - 604800"
    )


async def add_group(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not database.license_is_valid(update.effective_user.id):
        await update.message.reply_text("Aktif lisansın bulunmuyor.")
        return

    context.user_data["state"] = "group_id"
    await update.message.reply_text(
        "İzin aldığın grubun Telegram grup ID'sini gönder:"
    )


async def cancel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    context.user_data.clear()
    await update.message.reply_text("İşlem iptal edildi.")


def main():
    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("id", my_id))
    application.add_handler(CommandHandler("aralik", set_interval))
    application.add_handler(CommandHandler("tur_bekleme", set_cycle_wait))
    application.add_handler(CommandHandler("izinli_grup", add_group))
    application.add_handler(CommandHandler("iptal", cancel))

    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler)
    )

    logger.info("FARKETMEZ PANEL başlatılıyor.")
    application.run_polling()


if __name__ == "__main__":
    main()
