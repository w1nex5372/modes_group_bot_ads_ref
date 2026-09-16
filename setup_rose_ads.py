import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv
from telethon import TelegramClient
from telegram import Bot

load_dotenv()

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"].strip()
SESSION_NAME = os.getenv("SESSION_NAME", "tg_autoforward").strip()
GROUP = os.getenv("GROUP", os.getenv("GROUP_CHAT", "@NERADAUDROPO")).strip()
GROUP_URL = os.getenv("GROUP_PUBLIC_URL", "").strip()
if not GROUP_URL and GROUP.startswith("@"):
    GROUP_URL = "https://t.me/" + GROUP[1:]
BOT_TOKEN = os.environ["BOT_TOKEN"].strip()
ROSE_BOT_USERNAME = os.getenv("ROSE_BOT_USERNAME", "").strip().lstrip("@")
ASSETS = Path(os.getenv("ASSETS_DIR", "assets"))


def rose_cmd(command):
    if ROSE_BOT_USERNAME:
        name, *rest = command.split(" ", 1)
        suffix = rest[0] if rest else ""
        return f"{name}@{ROSE_BOT_USERNAME}" + (f" {suffix}" if suffix else "")
    return command


async def bot_username():
    async with Bot(BOT_TOKEN) as bot:
        me = await bot.get_me()
        return me.username


def contest_caption(username):
    bot_url = f"https://t.me/{username}?start=invite"
    return "\n".join([
        "🏆 SAVAITĖS INVITE KONKURSAS",
        "",
        "Rink taškus ir kilk į TOP 👀",
        "",
        "🔗 Invite nuoroda → +1 už naują narį",
        "➕ Add Members → +1 už naują narį",
        "⭐ Abu būdai sumuojasi",
        "",
        "🏆 TOP atsinaujina automatiškai",
        "🔄 Reset: pirmadienį 00:00",
        "⚠️ Tas pats žmogus skaičiuojamas tik 1 kartą",
        "",
        f"[🎯 DALYVAUTI](buttonurl://{bot_url})",
        f"[👑 NĖRA DROPO](buttonurl://{GROUP_URL}:same)",
    ])


def promo_caption(username):
    bot_url = f"https://t.me/{username}?start=invite"
    return "\n".join([
        "👑 NĖRA DROPO · INVITE SISTEMA",
        "",
        "Kaip rinkti taškus?",
        "",
        "🔗 1. Bote pasiimk „Mano invite“ ir dalinkis",
        "➕ 2. Arba pridėk žmogų tiesiai į grupę per Add Members",
        "⭐ 3. Kiekvienas naujas narys = +1 taškas",
        "",
        "🏆 Savaitės TOP matomas live",
        "🔄 Pirmadienį savaitės taškai prasideda nuo 0",
        "⚠️ Tas pats žmogus užskaitomas vieną kartą",
        "",
        f"[🤖 ATIDARYTI BOTĄ](buttonurl://{bot_url})",
        f"[🚀 ATIDARYTI GRUPĘ](buttonurl://{GROUP_URL}:same)",
    ])


async def wait_for_bot_reply(client, after_id, seconds=8):
    for _ in range(seconds * 2):
        await asyncio.sleep(0.5)
        for msg in await client.get_messages(GROUP, limit=12):
            if msg.id <= after_id:
                continue
            sender = await msg.get_sender()
            if sender and getattr(sender, "bot", False):
                if getattr(msg, "reply_to_msg_id", None) == after_id:
                    return msg
                if ROSE_BOT_USERNAME and (getattr(sender, "username", "") or "").lower() == ROSE_BOT_USERNAME.lower():
                    return msg
    return None


async def save_note(client, name, image_path, caption):
    print(f"\nKuriamas Rose note: {name}")
    source = await client.send_file(GROUP, image_path, caption=caption)
    save = await client.send_message(GROUP, rose_cmd(f"/save {name}"), reply_to=source.id)
    confirm = await wait_for_bot_reply(client, save.id)
    if not confirm:
        print(f"⚠️ Rose nepatvirtino /save {name}. Palieku testinį postą grupėje.")
        return False

    try:
        await client.delete_messages(GROUP, [source.id, save.id], revoke=True)
    except Exception:
        pass

    get_msg = await client.send_message(GROUP, rose_cmd(f"/get {name}"))
    result = await wait_for_bot_reply(client, get_msg.id)
    try:
        await client.delete_messages(GROUP, [get_msg.id], revoke=True)
    except Exception:
        pass

    if result:
        print(f"✅ {name}: Rose /get patikrintas. Preview paliktas grupėje.")
        return True

    print(f"⚠️ {name}: note išsaugotas, bet /get atsakymo nepavyko patvirtinti.")
    return False


async def main():
    username = await bot_username()
    contest = ASSETS / "ads" / "contest.png"
    promo = ASSETS / "ads" / "promo.png"
    if not contest.exists() or not promo.exists():
        raise SystemExit("Nerasti assets/ads/contest.png arba promo.png. Įkelk assets ir bandyk dar kartą.")

    client = TelegramClient(SESSION_NAME, API_ID, API_HASH)
    await client.start()
    me = await client.get_me()
    print(f"Telegram account: @{me.username or me.id}")
    print(f"Grupė: {GROUP}")

    try:
        ok1 = await save_note(client, "konkursas", str(contest), contest_caption(username))
        ok2 = await save_note(client, "promo", str(promo), promo_caption(username))
        print("\nREZULTATAS:")
        print(f"konkursas: {'OK' if ok1 else 'CHECK'}")
        print(f"promo: {'OK' if ok2 else 'CHECK'}")
        print("\nBote nustatyk:")
        print("/adsset konkursas promo")
        print("/adsfast 15")
        print("/adson")
        print("/adsnext")
        print("\nCustom emoji į Rose tekstą gali susidėti ranka po /get preview.")
    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
