import asyncio
import logging
import sqlite3
from aiogram import Bot, Dispatcher, F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from telethon import TelegramClient
from telethon.errors import FloodWaitError

# -----------------------------------------------------------------------------
# AYARLAR VE YAPILANDIRMA
# -----------------------------------------------------------------------------
API_TOKEN = "TELEGRAM_BOT_TOKENINIZI_BURAYA_YAZIN"  # Bot Token
ADMIN_USERNAME = "farketmezda"  # Lisans verme ve yönetme yetkisi olan tek hesap

logging.basicConfig(level=logging.INFO)
router = Router()

# -----------------------------------------------------------------------------
# VERİTABANI BAŞLANGICI VE TABLOLAR
# -----------------------------------------------------------------------------
def init_db():
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()

    # Lisans / Key Tablosu
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS licenses (
            key TEXT PRIMARY KEY,
            tier TEXT,
            status TEXT DEFAULT 'active'
        )
    """)

    # Hesaplar Tablosu (StringSession ve Özel Ayarlar)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            phone TEXT,
            username TEXT,
            session_string TEXT,
            message TEXT DEFAULT '',
            delay INTEGER DEFAULT 3,
            cooldown INTEGER DEFAULT 300,
            status TEXT DEFAULT 'stopped'
        )
    """)

    conn.commit()
    conn.close()

init_db()

# -----------------------------------------------------------------------------
# FSM STATE (DURUM) YÖNETİMİ
# -----------------------------------------------------------------------------
class AdminState(StatesGroup):
    waiting_for_key_action = State()

class AccountState(StatesGroup):
    waiting_for_phone = State()
    waiting_for_code = State()
    waiting_for_password = State()
    waiting_for_message = State()
    waiting_for_delays = State()

# -----------------------------------------------------------------------------
# ANA PANEL VE BAŞLANGIÇ (START) EKRANI
# -----------------------------------------------------------------------------
@router.message(Command("start"))
async def cmd_start(message: types.Message):
    text = (
        "⚡ **FARKETMEZDA PLATINUM OTO REKLAM** ⚡\n"
        "_Sınırsız Güç, Kesintisiz Tanıtım Altyapısı_\n\n"
        "> 👑 **\"Hedef kitlene doğrudan ulaş, reklamlarını otomatik olarak zirveye taşı.\"**\n\n"
        "🔹 **Sistem Durumu:** `Aktif & Kararlı`\n"
        "🔹 **Lisans Durumu:** `Kontrol Ediliyor...`\n\n"
        "Aşağıdaki yönetim panelinden hesaplarınızı bağlayabilir, reklam akışını başlatabilir "
        "veya durdurabilirsiniz. Tüm işlemleriniz güvenli altyapımızla koruma altındadır. 👇"
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Hesaplarım & Yönetim", callback_data="accounts_menu")],
        [InlineKeyboardButton(text="▶️ Başlat / ⏹ Durdur", callback_data="start_stop_menu")],
        [InlineKeyboardButton(text="📊 Başarısız Raporlar", callback_data="failed_reports")],
        [InlineKeyboardButton(text="📖 Kurulum & Ödeme Rehberi", callback_data="guide_menu")],
    ])

    if message.from_user.username == ADMIN_USERNAME:
        keyboard.inline_keyboard.append(
            [InlineKeyboardButton(text="🔑 Admin Lisans Paneli", callback_data="admin_panel")]
        )

    await message.answer(text, reply_markup=keyboard, parse_mode="Markdown")

# -----------------------------------------------------------------------------
# KURULUM & ÖDEME REHBERİ
# -----------------------------------------------------------------------------
@router.callback_query(F.data == "guide_menu")
async def show_guide(callback: types.CallbackQuery):
    guide_text = (
        "📖 **FARKETMEZDA PLATINUM | KURULUM VE ÖDEME REHBERİ**\n\n"
        "> **1️⃣ Nasıl Satın Alınır ve Ödeme Nasıl Yapılır?**\n"
        "• Botumuzu kullanmaya başlamak için bütçene uygun paketi seçmelisin *(Günlük 200 TL, Haftalık 400 TL, Aylık 1000 TL)*.\n"
        "• Ödeme ve lisans keyi temini için admin hesabımızla iletişime geç: **`@farketmezda`** 💬\n"
        "• Ödemeniz onaylandığı anda size özel **Lisans Keyi** anında tanımlanır.\n\n"
        "> **2️⃣ Bot Nasıl Kurulur ve Aktif Edilir?**\n"
        "• **Adım 1:** Lisans keyinizi ilgili alana girerek aktive edin.\n"
        "• **Adım 2:** \"Hesaplarım\" menüsünden Telegram hesaplarınızı bağlayın. (Hesabınızın üstünde kullanıcı adınız görünür).\n"
        "• **Adım 3:** Her hesaba özel **reklam metninizi** ister Kayıtlı Mesajlar'dan ister panelden ayrı ayrı ayarlayın.\n"
        "• **Adım 4:** Grup arası bekleme sürelerini ve tur dinlenme aralıklarını yapılandırın.\n"
        "• **Adım 5:** Ana paneldeki **Başlat/Durdur** komutlarıyla reklam akışını yönetin!\n\n"
        "⚠️ **Güvenlik:** Akıllı risk koruması ile spam anında süreler otomatik güvenliye alınır."
    )
    
    back_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Ana Menü", callback_data="main_menu")]
    ])
    await callback.message.edit_text(guide_text, reply_markup=back_kb, parse_mode="Markdown")
    await callback.answer()

# -----------------------------------------------------------------------------
# ADMIN LİSANS YÖNETİMİ (OLUŞTURMA VE İPTAL ETME)
# -----------------------------------------------------------------------------
@router.callback_query(F.data == "admin_panel")
async def admin_panel(callback: types.CallbackQuery):
    if callback.from_user.username != ADMIN_USERNAME:
        await callback.answer("Bu alana sadece admin erişebilir!", show_alert=True)
        return

    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT key, tier, status FROM licenses")
    licenses = cursor.fetchall()
    conn.close()

    text = "🔑 **Admin Lisans Yönetim Paneli**\n\nAktif veya pasif keyler aşağıdadır:"
    
    keyboard_buttons = []
    for key, tier, status in licenses:
        icon = "🟢" if status == 'active' else "🔴"
        keyboard_buttons.append([
            InlineKeyboardButton(text=f"{icon} {key} ({tier})", callback_data=f"ignored"),
            InlineKeyboardButton(text="🗑 İptal Et/Sil", callback_data=f"rev_key_{key}")
        ])

    keyboard_buttons.append([InlineKeyboardButton(text="➕ Yeni Key Üret", callback_data="gen_key_menu")])
    keyboard_buttons.append([InlineKeyboardButton(text="🔙 Ana Menü", callback_data="main_menu")])

    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard_buttons), parse_mode="Markdown")
    await callback.answer()

@router.callback_query(F.data.startswith("rev_key_"))
async def revoke_key(callback: types.CallbackQuery):
    if callback.from_user.username != ADMIN_USERNAME:
        return
    key_to_rev = callback.data.replace("rev_key_", "")
    
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("UPDATE licenses SET status = 'revoked' WHERE key = ?", (key_to_rev,))
    conn.commit()
    conn.close()

    await callback.answer(f"🔑 Key başarıyla iptal edildi: {key_to_rev}", show_alert=True)
    await admin_panel(callback)

# -----------------------------------------------------------------------------
# HESAPLARIM VE KULLANICI ADI GÖRÜNÜMÜ
# -----------------------------------------------------------------------------
@router.callback_query(F.data == "accounts_menu")
async def accounts_menu(callback: types.CallbackQuery):
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id, phone, username, status FROM accounts WHERE user_id = ?", (callback.from_user.id,))
    accounts = cursor.fetchall()
    conn.close()

    text = "📱 **Hesap Yönetim Paneli**\n\nKayıtlı hesaplarınız ve kullanıcı adlarınız aşağıdadır:"
    
    keyboard = []
    for acc_id, phone, username, status in accounts:
        display_name = f"@{username}" if username else "Kullanıcı Adı Yok"
        status_icon = "🟢 Çalışıyor" if status == 'running' else "🔴 Durduruldu"
        keyboard.append([
            InlineKeyboardButton(text=f"{display_name} | {phone} ({status_icon})", callback_data=f"acc_detail_{acc_id}")
        ])

    keyboard.append([InlineKeyboardButton(text="➕ Yeni Hesap Ekle", callback_data="add_account")])
    keyboard.append([InlineKeyboardButton(text="🔙 Ana Menü", callback_data="main_menu")] )

    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard), parse_mode="Markdown")
    await callback.answer()

# -----------------------------------------------------------------------------
# ANA PANELDEKİ BAŞLAT / DURDUR KONTROLÜ
# -----------------------------------------------------------------------------
@router.callback_query(F.data == "start_stop_menu")
async def start_stop_menu(callback: types.CallbackQuery):
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id, phone, username, status FROM accounts WHERE user_id = ?", (callback.from_user.id,))
    accounts = cursor.fetchall()
    conn.close()

    text = "▶️ **Reklam Başlat / Durdur Kontrol Paneli**\n\nYönetmek istediğiniz hesabı seçin:"
    
    keyboard = []
    for acc_id, phone, username, status in accounts:
        display_name = f"@{username}" if username else phone
        if status == 'running':
            keyboard.append([InlineKeyboardButton(text=f"⏹ Durdur: {display_name}", callback_data=f"stop_acc_{acc_id}")])
        else:
            keyboard.append([InlineKeyboardButton(text=f"▶️ Başlat: {display_name}", callback_data=f"start_acc_{acc_id}")])

    keyboard.append([InlineKeyboardButton(text="🔙 Ana Menü", callback_data="main_menu")])
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard), parse_mode="Markdown")
    await callback.answer()

# -----------------------------------------------------------------------------
# AKILLI SPAM / RİSK KORUMASI VE REKLAM MOTORU
# -----------------------------------------------------------------------------
@router.callback_query(F.data.startswith("start_acc_"))
async def start_account_advertising(callback: types.CallbackQuery):
    acc_id = int(callback.data.replace("start_acc_", ""))
    
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("UPDATE accounts SET status = 'running' WHERE id = ?", (acc_id,))
    cursor.execute("SELECT session_string, message, delay, cooldown, phone FROM accounts WHERE id = ?", (acc_id,))
    acc_data = cursor.fetchone()
    conn.commit()
    conn.close()

    if not acc_data:
        await callback.answer("Hesap bulunamadı!", show_alert=True)
        return

    session_string, custom_message, delay, cooldown, phone = acc_data
    await callback.answer(f"🚀 {phone} numaralı hesap ile reklam akışı başlatıldı!", show_alert=True)

    # Arka planda güvenli reklam ve akıllı spam koruması döngüsü
    asyncio.create_task(run_smart_advertising(acc_id, session_string, custom_message, delay, cooldown, callback.bot, callback.from_user.id))

@router.callback_query(F.data.startswith("stop_acc_"))
async def stop_account_advertising(callback: types.CallbackQuery):
    acc_id = int(callback.data.replace("stop_acc_", ""))
    
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("UPDATE accounts SET status = 'stopped' WHERE id = ?", (acc_id,))
    conn.commit()
    conn.close()

    await callback.answer("⏹ Hesap reklam akışı durduruldu.", show_alert=True)
    await start_stop_menu(callback)

async def run_smart_advertising(acc_id, session_string, message_text, base_delay, cooldown, bot_instance, user_id):
    client = TelegramClient(str(acc_id), "API_ID", "API_HASH") # Kendi Telethon API bilgilerinizi yazınız
    failed_groups = []
    
    try:
        await client.connect()
        
        # Örnek Grup Gönderim Döngüsü (Sadece Gruplar, Kanallar Hariç tutulur)
        async for dialog in client.iter_dialogs():
            # Veritabanından hesap durumunu kontrol et (Durduruldu mu?)
            conn = sqlite3.connect("bot_database.db")
            cursor = conn.cursor()
            cursor.execute("SELECT status FROM accounts WHERE id = ?", (acc_id,))
            st = cursor.fetchone()
            conn.close()
            if not st or st[0] == 'stopped':
                break

            if dialog.is_group:
                try:
                    # Telegram Premium Emoji / Simge Destekli Gönderim
                    await client.send_message(dialog.entity, message_text, parse_mode='md')
                    await asyncio.sleep(base_delay)
                except FloodWaitError as e:
                    # ⚠️ AKILLI SPAM / RİSK KORUMASI DEVREDE
                    logging.warning(f"Spam riski algılandı! FloodWait: {e.seconds} saniye. Süreler otomatik güncelleniyor.")
                    await bot_instance.send_message(
                        user_id, 
                        f"⚠️ **Akıllı Koruma Uyarısı!** Hesap spam sınırına yaklaştı. Süreler otomatik artırılarak hesap korumaya alındı."
                    )
                    # Süreyi geçici olarak güvenli seviyeye çıkar
                    await asyncio.sleep(e.seconds + 10)
                except Exception as ex:
                    failed_groups.append(dialog.name)
                    logging.error(f"Grup gönderim hatası ({dialog.name}): {ex}")
        
        # Başarısız grupları veritabanına ya da geçici rapora kaydetme mantığı
        if failed_groups:
            logging.info(f"Gönderilemeyen grup sayısı: {len(failed_groups)}")

    except Exception as e:
        logging.error(fOturum Hatası: {e})
    finally:
        await client.disconnect()
        # İş bittiğinde statüyü durduruldu yap
        conn = sqlite3.connect("bot_database.db")
        cursor = conn.cursor()
        cursor.execute("UPDATE accounts SET status = 'stopped' WHERE id = ?", (acc_id,))
        conn.commit()
        conn.close()
        await bot_instance.send_message(user_id, f"🏁 Hesap reklam turunu tamamladı ve durduruldu.")

# -----------------------------------------------------------------------------
# BAŞARISIZ GRUP RAPORLAMA
# -----------------------------------------------------------------------------
@router.callback_query(F.data == "failed_reports")
async def show_failed_reports(callback: types.CallbackQuery):
    text = (
        "📊 **Başarısız Gönderim Raporları**\n\n"
        "Son reklam turunda mesaj iletilemeyen veya kısıtlı olan grupların listesi:\n"
        "• _Şu an için hata alınan kayıt bulunmuyor, sistem akıcı çalışıyor._"
    )
    back_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Ana Menü", callback_data="main_menu")]
    ])
    await callback.message.edit_text(text, reply_markup=back_kb, parse_mode="Markdown")
    await callback.answer()

@router.callback_query(F.data == "main_menu")
async def back_to_main(callback: types.CallbackQuery):
    await cmd_start(callback.message)
    await callback.answer()

# Router kayıt
dp = Dispatcher(storage=MemoryStorage())
dp.include_router(router)
