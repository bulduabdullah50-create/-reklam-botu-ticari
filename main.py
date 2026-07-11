# ----------------- SABİT AYARLAR -----------------
API_ID = 38225321
API_import os
HASH = "48499b759e2281784608038c660df2d"
BOT_TOKEN = os.getenv("BOT_TOKEN")

# KENDİ TELEGRAM ID'NİZİ BURAYA YAZIN (Örn: 512345678)
ADMIN_ID = 8975803815  
# -------------------------------------------------

bot = TelegramClient('reklamci_ticari_session', API_ID, API_HASH).start(bot_token=BOT_TOKEN)

izinli_kullanicilar = {} # {user_id: kalan_gun}
user_clients = {}        # {user_id: data}
reklam_durumu = {}       # {user_id: bool}

@bot.on(events.NewMessage(pattern='/start'))
async def start(event):
    user_id = event.chat_id
    isim = event.sender.first_name if event.sender.first_name else "KULLANICI"
    
    if user_id != ADMIN_ID and user_id not in izinli_kullanicilar:
        return await event.respond(
            "Bu botu kullanmaya izniniz yok. Üyelik için:\n@sherkeys0"
        )
        
    await event.respond(
        f"**HOŞGELDİN {isim.upper()}**\n\n"
        "BEN TÜM TELEGRAMA REKLAM YAPABİLEN BİR BOTUM. LÜTFEN KULLANIMA BAŞLAMADAN ÖNCE /help YAZARAK BOTU ÖĞRENİNİZ.\n\n"
        "HALA ÜYELİK ALMADIYSANIZ @sherkeys0 DEN ÜYELİK ALARAK REKLAM YAPMAYA BAŞLAYABİLİRSİNİZ.."
    )

@bot.on(events.NewMessage(pattern='/help'))
async def help_menu(event):
    user_id = event.chat_id
    if user_id != ADMIN_ID and user_id not in izinli_kullanicilar: return
    
    await event.respond(
        "**Yardım menüsüne Hoşgeldiniz**\n\n"
        "🔹 `/start` - Oturum başlatın\n"
        "🔹 `/hesapekle <telefon numarası>` - Yeni bir hesap ekleyin\n"
        "🔹 `/kod <doğrulama kodu>` - Doğrulama kodunu girin\n"
        "🔹 `/forward <mesaj_linki>` - İletilecek kanal/grup mesajının linkini belirleyin\n"
        "🔹 `/aralik <saniye>` - Reklam gönderme aralığını ayarlayın\n"
        "🔹 `/zaman <saniye>` - Reklam gönderme işleminin yeniden başlama süresini ayarlayın\n"
        "🔹 `/reklam <mesaj>` - Direkt metin olarak reklam ayarlayın\n"
        "🔹 `/baslat` - Reklam gönderme işlemini başlatır\n"
        "🔹 `/stop` - Mesaj gönderme işlemini durdurur\n"
        "🔹 `/help` - Komut listesini gösterir\n\n"
        f"{'👑 **Yönetici Komutları:**\n🔸 `/izinver <kullanici_id> <gün>`\n🔸 `/izinal <kullanici_id>`\n🔸 `/duyuru <mesaj>`' if user_id == ADMIN_ID else ''}"
    )

# 👑 ADMIN KOMUTLARI
@bot.on(events.NewMessage(pattern=r'/izinver (\d+)(?: (\d+))?'))
async def izin_ver(event):
    if event.chat_id != ADMIN_ID: return
    target = int(event.pattern_match.group(1))
    gun = int(event.pattern_match.group(2)) if event.pattern_match.group(2) else 30
    izinli_kullanicilar[target] = gun
    await event.respond(f"✅ `{target}` ID'li kullanıcıya **{gun} gün** izin verildi.")

@bot.on(events.NewMessage(pattern=r'/izinal (\d+)'))
async def izin_al(event):
    if event.chat_id != ADMIN_ID: return
    target = int(event.pattern_match.group(1))
    if target in izinli_kullanicilar: del izinli_kullanicilar[target]
    await event.respond(f"🛑 `{target}` ID'li kullanıcının izni iptal edildi.")

@bot.on(events.NewMessage(pattern=r'/duyuru (.+)'))
async def duyuru(event):
    if event.chat_id != ADMIN_ID: return
    mesaj = event.pattern_match.group(1)
    for uid in list(user_clients.keys()) + list(izinli_kullanicilar.keys()):
        try: await bot.send_message(uid, f"📢 **Duyuru:**\n\n{mesaj}")
        except: pass
    await event.respond("📢 Duyuru tüm kullanıcılara iletildi.")

# ⚙️ MÜŞTERİ KOMUTLARI
@bot.on(events.NewMessage(pattern=r'/hesapekle (.+)'))
async def hesap_ekle(event):
    user_id = event.chat_id
    if user_id != ADMIN_ID and user_id not in izinli_kullanicilar: return
    
    numara = event.pattern_match.group(1).strip()
    await event.respond("🔄 Kod talep ediliyor, lütfen bekleyin...")
    
    client = TelegramClient(f'user_session_{user_id}', API_ID, API_HASH)
    await client.connect()
    
    try:
        sent_code = await client.send_code_request(numara)
        user_clients[user_id] = {
            'client': client, 'phone': numara, 
            'phone_code_hash': sent_code.phone_code_hash,
            'aralik': 60, 'zaman': 10
        }
        await event.respond("📩 Kod gönderildi! Lütfen `/kod <gelen_kod>` şeklinde bota girin.")
    except Exception as e:
        await event.respond(f"❌ Hata: {str(e)}")

@bot.on(events.NewMessage(pattern=r'/kod (.+)'))
async def kod_gir(event):
    user_id = event.chat_id
    if user_id not in user_clients: return
    
    kod = event.pattern_match.group(1).strip()
    data = user_clients[user_id]
    
    try:
        await data['client'].sign_in(phone=data['phone'], code=kod, phone_code_hash=data['phone_code_hash'])
        await event.respond("✅ Hesabınız başarıyla bota bağlandı!")
    except Exception as e:
        await event.respond(f"❌ Giriş başarısız: {str(e)}")

@bot.on(events.NewMessage(pattern=r'/reklam (.+)'))
async def reklam_metni(event):
    user_id = event.chat_id
    if user_id not in user_clients: return
    user_clients[user_id]['reklam_metni'] = event.pattern_match.group(1)
    if 'forward' in user_clients[user_id]: del user_clients[user_id]['forward']
    await event.respond("📝 Reklam metniniz kaydedildi.")

@bot.on(events.NewMessage(pattern=r'/aralik (\d+)'))
async def aralik_set(event):
    user_id = event.chat_id
    if user_id not in user_clients: return
    user_clients[user_id]['aralik'] = int(event.pattern_match.group(1))
    await event.respond(f"⏱ Reklam gönderme aralığı {event.pattern_match.group(1)} saniye yapıldı.")

@bot.on(events.NewMessage(pattern=r'/zaman (\d+)'))
async def zaman_set(event):
    user_id = event.chat_id
    if user_id not in user_clients: return
    user_clients[user_id]['zaman'] = int(event.pattern_match.group(1))
    await event.respond(f"🔄 Reklam gönderme işleminin yeniden başlama süresi {event.pattern_match.group(1)} saniye yapıldı.")

@bot.on(events.NewMessage(pattern=r'/forward (.+)'))
async def forward_set(event):
    user_id = event.chat_id
    if user_id not in user_clients: return
    link = event.pattern_match.group(1).strip()
    
    # t.me/c/1234567/12 veya t.me/kanaladi/12 linklerini ayrıştırır
    match = re.search(r't\.me/(?:c/)?([^/]+)/(\d+)', link)
    if match:
        target_chat = match.group(1)
        msg_id = int(match.group(2))
        if target_chat.isdigit():
            target_chat = int(f"-100{target_chat}")
        user_clients[user_id]['forward'] = {'msg_id': msg_id, 'from_chat': target_chat}
        if 'reklam_metni' in user_clients[user_id]: del user_clients[user_id]['reklam_metni']
        await event.respond("🔄 İletilecek mesaj başarıyla ayarlandı.")
    else:
        await event.respond("❌ Geçersiz mesaj linki. Örnek: `/forward https://t.me/kanal/123`")

@bot.on(events.NewMessage(pattern='/baslat'))
async def reklam_baslat(event):
    user_id = event.chat_id
    if user_id not in user_clients or 'client' not in user_clients[user_id]:
        return await event.respond("❌ Bağlı hesap bulunamadı.")
        
    reklam_durumu[user_id] = True
    await event.respond("🚀 Reklam gönderme işlemi başlatıldı ve arkanıza yaslanın.")
    
    data = user_clients[user_id]
    client = data['client']
    
    while reklam_durumu.get(user_id):
        async for dialog in client.iter_dialogs():
            if not reklam_durumu.get(user_id): break
            if dialog.is_group:
                try:
                    if 'forward' in data:
                        await client.forward_messages(dialog.id, data['forward']['msg_id'], data['forward']['from_chat'])
                    elif 'reklam_metni' in data:
                        await client.send_message(dialog.id, data['reklam_metni'])
                except: pass
                await asyncio.sleep(data['aralik'])
        
        await asyncio.sleep(data['zaman'])

@bot.on(events.NewMessage(pattern='/stop'))
async def reklam_dur(event):
    reklam_durumu[event.chat_id] = False
    await event.respond("🛑 Mesaj gönderme işlemi durduruldu.")

print("Bot aktif ve sunucuda çalışıyor...")
bot.run_until_disconnected()
