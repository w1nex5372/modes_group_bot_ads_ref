import asyncio
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from telethon import TelegramClient, errors

load_dotenv()

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"].strip()
SESSION_NAME = os.getenv("SESSION_NAME", "tg_autoforward").strip()
STATE_FILE = Path(".tg_login_state.json")


def usage():
    print("Naudojimas:")
    print("  telegram_login.py send +3706XXXXXXX")
    print("  telegram_login.py finish 12345")
    print("  telegram_login.py finish 12345 TAVO_2FA_PASSWORD")


async def send_code(phone: str):
    client = TelegramClient(SESSION_NAME, API_ID, API_HASH)
    await client.connect()
    try:
        if await client.is_user_authorized():
            me = await client.get_me()
            print(f"✅ Session jau prisijungusi kaip @{me.username or me.id}")
            return
        result = await client.send_code_request(phone)
        STATE_FILE.write_text(
            json.dumps({"phone": phone, "phone_code_hash": result.phone_code_hash}, ensure_ascii=False),
            encoding="utf-8",
        )
        print("✅ Telegram kodas išsiųstas.")
        print("Dabar paleisk: telegram_login.bat finish KODAS")
    finally:
        await client.disconnect()


async def finish(code: str, password: str | None):
    if not STATE_FILE.exists():
        raise SystemExit("❌ Nerasta .tg_login_state.json. Pirma paleisk telegram_login.bat send +370...")
    state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    phone = state["phone"]
    phone_code_hash = state["phone_code_hash"]

    client = TelegramClient(SESSION_NAME, API_ID, API_HASH)
    await client.connect()
    try:
        if await client.is_user_authorized():
            me = await client.get_me()
            print(f"✅ Session jau prisijungusi kaip @{me.username or me.id}")
            STATE_FILE.unlink(missing_ok=True)
            return
        try:
            await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
        except errors.SessionPasswordNeededError:
            if not password:
                raise SystemExit(
                    "🔐 Paskyroje įjungtas 2FA. Paleisk: telegram_login.bat finish KODAS TAVO_2FA_PASSWORD"
                )
            await client.sign_in(password=password)

        me = await client.get_me()
        print(f"✅ Prisijungta kaip: {me.first_name or ''} (@{me.username or 'be_username'})")
        print(f"✅ Session išsaugota: {SESSION_NAME}.session")
        STATE_FILE.unlink(missing_ok=True)
    finally:
        await client.disconnect()


async def main():
    if len(sys.argv) < 3:
        usage()
        raise SystemExit(2)
    action = sys.argv[1].lower()
    if action == "send":
        await send_code(sys.argv[2])
    elif action == "finish":
        password = sys.argv[3] if len(sys.argv) >= 4 else None
        await finish(sys.argv[2].replace(" ", ""), password)
    else:
        usage()
        raise SystemExit(2)


if __name__ == "__main__":
    asyncio.run(main())
