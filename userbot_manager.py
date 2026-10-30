import asyncio
from telethon import TelegramClient
from telethon.sessions import StringSession

active_workers = {}


async def run_userbot_task(
    user_id,
    api_id,
    api_hash,
    session_str,
    target_groups,
    message,
    interval
):
    client = TelegramClient(
        StringSession(session_str),
        int(api_id),
        api_hash
    )

    try:
        await client.connect()

        if not await client.is_user_authorized():
            raise RuntimeError("Telegram oturumu geçersiz.")

        while active_workers.get(user_id, {}).get("running"):
            for group in target_groups:
                if not active_workers.get(user_id, {}).get("running"):
                    break

                try:
                    await client.send_message(group, message)
                    print(f"[{user_id}] Gönderildi: {group}")

                except Exception as error:
                    print(f"[{user_id}] Hata: {error}")

            await asyncio.sleep(max(1, int(interval)) * 60)

    finally:
        await client.disconnect()
        active_workers.pop(user_id, None)


def start_userbot(
    user_id,
    api_id,
    api_hash,
    session_str,
    groups,
    message,
    interval
):
    if user_id in active_workers:
        return False, "Zaten çalışıyor!"

    if not session_str or not groups or not message:
        return False, "Hesap, grup veya mesaj bilgisi eksik."

    active_workers[user_id] = {
        "running": True
    }

    task = asyncio.create_task(
        run_userbot_task(
            user_id,
            api_id,
            api_hash,
            session_str,
            groups,
            message,
            interval
        )
    )

    active_workers[user_id]["task"] = task

    return True, "Userbot başlatıldı!"


async def stop_userbot(user_id):
    worker = active_workers.get(user_id)

    if not worker:
        return False, "Çalışan bot bulunamadı."

    worker["running"] = False

    task = worker.get("task")

    if task:
        task.cancel()

        try:
            await task
        except asyncio.CancelledError:
            pass

    return True, "Userbot durduruldu."


def is_running(user_id):
    return user_id in active_workers
