import asyncio
import json
import os
import sys
import time
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
    print("  telegram_login.py resend")
    print("  telegram_login.py finish 12345")
    print("  telegram_login.py finish 12345 TAVO_2FA_PASSWORD")


def code_type_name(value):
    if value is None:
        return "nežinoma"
    name = type(value).__name__
    mapping = {
        "SentCodeTypeApp": "Telegram programėlę (service chat 'Telegram')",
        "SentCodeTypeSms": "SMS",
        "SentCodeTypeCall": "telefono skambutį",
        "SentCodeTypeFlashCall": "flash call",
        "SentCodeTypeMissedCall": "praleistą skambutį",
        "SentCodeTypeEmailCode": "el. paštą",
        "SentCodeTypeFragmentSms": "Fragment SMS",
    }
    return mapping.get(name, name)


def save_state(phone, result):
    STATE_FILE.write_text(
        json.dumps(
            {
                "phone": phone,
                "phone_code_hash": result.phone_code_hash,
                "sent_at": int(time.time()),
                "timeout": int(getattr(result, "timeout", 0) or 0),
                "delivery": code_type_name(getattr(result, "type", None)),
                "next_delivery": code_type_name(getattr(result, "next_type", None)),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def print_delivery(result):
    print("✅ Telegram prisijungimo kodas paprašytas.")
    print(f"📩 Telegram nurodė pristatymą į: {code_type_name(getattr(result, 'type', None))}")
    next_type = getattr(result, "next_type", None)
    timeout = getattr(result, "timeout", None)
    if next_type is not None:
        print(f"↪️ Kitas galimas būdas: {code_type_name(next_type)}")
    if timeout:
        print(f"⏱ Kitas būdas gali tapti prieinamas maždaug po {timeout} s.")
    print("Jei rodo Telegram programėlę, ieškok oficialiame 'Telegram' service chate, ne SMS žinutėse.")


async def send_code(phone: str):
    client = TelegramClient(SESSION_NAME, API_ID, API_HASH)
    await client.connect()
    try:
        if await client.is_user_authorized():
            me = await client.get_me()
            print(f"✅ Session jau prisijungusi kaip @{me.username or me.id}")
            return
        result = await client.send_code_request(phone)
        save_state(phone, result)
        print_delivery(result)
        print("Kai gausi kodą: telegram_login.bat finish KODAS")
        if getattr(result, "next_type", None) is not None:
            print("Jei pirmu būdu kodo negausi, sulauk timeout ir paleisk: telegram_login.bat resend")
    except errors.FloodWaitError as e:
        raise SystemExit(f"⏳ Telegram riboja bandymus. Palauk {e.seconds} s. ir nebandyk kartoti anksčiau.")
    finally:
        await client.disconnect()


async def resend_code():
    if not STATE_FILE.exists():
        raise SystemExit("❌ Nerasta .tg_login_state.json. Pirma paleisk telegram_login.bat send +370...")
    state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    phone = state["phone"]
    phone_code_hash = state["phone_code_hash"]
    sent_at = int(state.get("sent_at", 0) or 0)
    timeout = int(state.get("timeout", 0) or 0)
    remaining = sent_at + timeout - int(time.time())
    if remaining > 0:
        raise SystemExit(f"⏱ Dar palauk apie {remaining} s. prieš resend.")

    client = TelegramClient(SESSION_NAME, API_ID, API_HASH)
    await client.connect()
    try:
        if await client.is_user_authorized():
            me = await client.get_me()
            print(f"✅ Session jau prisijungusi kaip @{me.username or me.id}")
            STATE_FILE.unlink(missing_ok=True)
            return
        result = await client.resend_code_request(phone, phone_code_hash)
        save_state(phone, result)
        print_delivery(result)
        print("Kai gausi kodą: telegram_login.bat finish KODAS")
    except errors.FloodWaitError as e:
        raise SystemExit(f"⏳ Telegram riboja resend. Palauk {e.seconds} s.")
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
    if len(sys.argv) < 2:
        usage()
        raise SystemExit(2)
    action = sys.argv[1].lower()
    if action == "send":
        if len(sys.argv) < 3:
            usage()
            raise SystemExit(2)
        await send_code(sys.argv[2])
    elif action == "resend":
        await resend_code()
    elif action == "finish":
        if len(sys.argv) < 3:
            usage()
            raise SystemExit(2)
        password = sys.argv[3] if len(sys.argv) >= 4 else None
        await finish(sys.argv[2].replace(" ", ""), password)
    else:
        usage()
        raise SystemExit(2)


if __name__ == "__main__":
    asyncio.run(main())
