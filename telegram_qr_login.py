import asyncio
import getpass
import os
from pathlib import Path

import qrcode
from dotenv import load_dotenv
from telethon import TelegramClient, errors

load_dotenv()

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"].strip()
SESSION_NAME = os.getenv("SESSION_NAME", "tg_autoforward").strip()
QR_FILE = Path("telegram_login_qr.png").resolve()


async def main():
    client = TelegramClient(SESSION_NAME, API_ID, API_HASH)
    await client.connect()
    try:
        if await client.is_user_authorized():
            me = await client.get_me()
            print(f"✅ Session jau prisijungusi kaip @{me.username or me.id}")
            return

        qr = await client.qr_login()
        qrcode.make(qr.url).save(QR_FILE)

        print("✅ QR sugeneruotas:")
        print(f"   {QR_FILE}")
        print()
        print("TELEGRAM TELEFONE:")
        print("1. Settings")
        print("2. Devices")
        print("3. Link Desktop Device")
        print("4. Nuskenuok atsidariusį QR")
        print()
        print("Svarbu: šis langas turi likti paleistas kol skenuoji.")

        if os.name == "nt":
            try:
                os.startfile(QR_FILE)
            except OSError:
                pass

        try:
            await qr.wait(timeout=120)
        except errors.SessionPasswordNeededError:
            password = getpass.getpass("🔐 Telegram 2FA password: ")
            await client.sign_in(password=password)
        except asyncio.TimeoutError:
            raise SystemExit("⏱ QR baigė galioti. Paleisk telegram_qr_login.bat dar kartą.")

        me = await client.get_me()
        print(f"✅ Prisijungta kaip: {me.first_name or ''} (@{me.username or 'be_username'})")
        print(f"✅ Session išsaugota: {SESSION_NAME}.session")
        print("Dabar paleisk: run_forward_only.bat")
    finally:
        await client.disconnect()
        try:
            QR_FILE.unlink(missing_ok=True)
        except OSError:
            pass


if __name__ == "__main__":
    asyncio.run(main())
