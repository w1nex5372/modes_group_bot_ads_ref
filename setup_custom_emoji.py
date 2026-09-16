import asyncio
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from telegram import Bot, InputSticker, Sticker
from telegram.error import BadRequest

load_dotenv()

BOT_TOKEN = os.environ["BOT_TOKEN"].strip()
ADMIN_IDS = [int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()]
ASSETS = Path(os.getenv("ASSETS_DIR", "assets")) / "emoji"
OUTPUT = Path(os.getenv("EMOJI_IDS_FILE", "emoji_ids.json"))

ITEMS = [
    ("brand", "brand.webp", "👑", ["nera", "dropo", "brand"]),
    ("crown", "crown.webp", "👑", ["crown", "top"]),
    ("invite", "invite.webp", "🔗", ["invite", "link"]),
    ("share", "share.webp", "📤", ["share", "send"]),
    ("trophy", "trophy.webp", "🏆", ["top", "winner"]),
    ("group", "group.webp", "👥", ["group", "members"]),
    ("add", "add.webp", "➕", ["add", "member"]),
    ("stats", "stats.webp", "📈", ["stats", "growth"]),
]


async def main():
    if not ADMIN_IDS:
        raise SystemExit(".env įrašyk ADMIN_IDS=<tavo Telegram user ID>.")
    owner_id = ADMIN_IDS[0]

    async with Bot(BOT_TOKEN) as bot:
        me = await bot.get_me()
        username = me.username
        set_name = f"nera_dropo_brand_by_{username}".lower()
        title = "NĖRA DROPO Brand"

        try:
            sticker_set = await bot.get_sticker_set(set_name)
            print(f"Emoji set jau yra: https://t.me/addemoji/{set_name}")
        except BadRequest:
            stickers = []
            for key, filename, fallback, keywords in ITEMS:
                path = ASSETS / filename
                if not path.exists():
                    raise SystemExit(f"Nerastas {path}. Padaryk git pull.")
                stickers.append(
                    InputSticker(
                        sticker=path.read_bytes(),
                        emoji_list=[fallback],
                        format="static",
                        keywords=keywords,
                    )
                )
            await bot.create_new_sticker_set(
                user_id=owner_id,
                name=set_name,
                title=title,
                stickers=stickers,
                sticker_type=Sticker.CUSTOM_EMOJI,
            )
            sticker_set = await bot.get_sticker_set(set_name)
            print(f"✅ Sukurtas custom emoji pack: https://t.me/addemoji/{set_name}")

        ids = {}
        for index, (key, *_rest) in enumerate(ITEMS):
            if index < len(sticker_set.stickers):
                ids[key] = sticker_set.stickers[index].custom_emoji_id

        OUTPUT.write_text(json.dumps(ids, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"✅ Custom emoji IDs išsaugoti: {OUTPUT}")
        print("Perkrauk start.bat — botas pradės naudoti juos antraštėse ir tekste.")
        print("Pastaba: Telegram inline mygtukai nepalaiko custom-emoji entity — juose lieka įprasti emoji.")


if __name__ == "__main__":
    asyncio.run(main())
