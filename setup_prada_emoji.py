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
ASSETS = Path(os.getenv("ASSETS_DIR", "assets")) / "emoji_prada"
OUTPUT = Path(os.getenv("EMOJI_IDS_FILE", "emoji_ids_prada.json"))

# Telegram requires every InputSticker emoji_list item to be a real Unicode emoji.
# Keep the visual design in the WEBP files; these are only valid fallback emojis
# used by Telegram while creating the custom-emoji sticker set.
ITEMS = [
    ("brand", "brand.webp", "🖤", ["prada", "luxury", "brand"]),
    ("crown", "crown.webp", "💎", ["luxury", "diamond", "milan"]),
    ("invite", "invite.webp", "🔗", ["invite", "link"]),
    ("share", "share.webp", "📤", ["share", "send"]),
    ("trophy", "trophy.webp", "🏆", ["top", "winner"]),
    ("group", "group.webp", "👥", ["group", "community"]),
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
        set_name = f"prada_lux_community_by_{username}".lower()
        title = "PRADA LUX Community"

        try:
            sticker_set = await bot.get_sticker_set(set_name)
            print(f"Emoji set jau yra: https://t.me/addemoji/{set_name}")
        except BadRequest:
            stickers = []
            for key, filename, fallback, keywords in ITEMS:
                path = ASSETS / filename
                if not path.exists():
                    raise SystemExit(f"Nerastas {path}. Paleisk generate_prada_assets.bat.")
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
        print("Perkrauk start.bat — Prada theme naudos juos tekste ir inline mygtukuose.")
        print("Pastaba: tai originalus neoficialios bendruomenės emoji rinkinys, ne oficialūs Prada assetai.")


if __name__ == "__main__":
    asyncio.run(main())
