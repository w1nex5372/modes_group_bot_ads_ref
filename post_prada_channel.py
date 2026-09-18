"""Publish PRADA LUX promo/contest posts directly to a Telegram channel.

Usage:
  python post_prada_channel.py promo
  python post_prada_channel.py contest
  python post_prada_channel.py promo --pin
  python post_prada_channel.py both --pin

The bot must be an admin in CHANNEL_CHAT with permission to post messages.
For --pin it also needs permission to pin/edit channel messages.
"""

import argparse
import asyncio
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.error import BadRequest

from generate_prada_assets import generate_channel_promo, generate_contest_poster

load_dotenv()

BOT_TOKEN = os.environ["BOT_TOKEN"].strip()
CHANNEL_CHAT = os.getenv("CHANNEL_CHAT", "").strip()
GROUP_PUBLIC_URL = os.getenv("GROUP_PUBLIC_URL", "").strip()
ASSETS_DIR = Path(os.getenv("ASSETS_DIR", "assets"))
EMOJI_IDS_FILE = Path(os.getenv("EMOJI_IDS_FILE", "emoji_ids_prada.json"))

if not CHANNEL_CHAT:
    raise SystemExit("❌ .env įrašyk CHANNEL_CHAT=@TAVO_KANALAS arba kanalo -100... ID")
if not GROUP_PUBLIC_URL:
    raise SystemExit("❌ .env įrašyk GROUP_PUBLIC_URL (public @group link arba private invite link).")


def emoji_ids():
    if not EMOJI_IDS_FILE.exists():
        return {}
    try:
        data = json.loads(EMOJI_IDS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


IDS = emoji_ids()


def button(text: str, url: str, key: str | None = None, custom: bool = True):
    icon = str(IDS.get(key, "")).strip() if key and custom else ""
    return InlineKeyboardButton(
        text=text,
        url=url,
        icon_custom_emoji_id=icon or None,
    )


def keyboard(bot_url: str, custom: bool = True):
    return InlineKeyboardMarkup(
        [
            [button("◆ PRISIJUNGTI PRIE GRUPĖS", GROUP_PUBLIC_URL, "group", custom)],
            [button("✦ GAUTI MANO INVITE", bot_url, "invite", custom)],
        ]
    )


def post_data(kind: str):
    if kind == "contest":
        return (
            ASSETS_DIR / "prada_ads" / "contest.webp",
            (
                "<b>SAVAITĖS INVITE TOP</b>\n\n"
                "Kviesk bendruomenės narius ir rink taškus.\n\n"
                "🔗 Naujas narys per tavo invite → <b>+1</b>\n"
                "➕ Add Members → <b>+1</b>\n"
                "🏆 TOP atsinaujina automatiškai\n"
                "↻ Nauja savaitė: pirmadienį 00:00\n\n"
                "<i>Neoficiali bendruomenė · nesusijusi su Prada S.p.A.</i>"
            ),
        )
    return (
        ASSETS_DIR / "prada_ads" / "promo.webp",
        (
            "<b>PRADA INFO</b>\n\n"
            "Visa svarbiausia bendruomenės informacija vienoje vietoje.\n\n"
            "Naujienos · Konkursai · Savaitės TOP\n\n"
            "Prisijunk prie pagrindinės bendruomenės žemiau.\n\n"
            "<i>Neoficiali bendruomenė · nesusijusi su Prada S.p.A.</i>"
        ),
    )


async def send_one(bot: Bot, kind: str, pin: bool):
    # Always rebuild the poster from repo code so git pull is enough to update visuals.
    if kind == "contest":
        generate_contest_poster()
    else:
        generate_channel_promo()

    me = await bot.get_me()
    bot_url = f"https://t.me/{me.username}?start=invite"
    image, caption = post_data(kind)
    if not image.exists():
        raise SystemExit(f"❌ Nerastas assetas: {image}")

    kwargs = dict(
        chat_id=CHANNEL_CHAT,
        photo=image.read_bytes(),
        caption=caption,
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard(bot_url, custom=True),
    )
    try:
        msg = await bot.send_photo(**kwargs)
    except BadRequest as exc:
        # Some bots/accounts may not be eligible for custom emoji icons on buttons.
        if "emoji" not in str(exc).lower():
            raise
        print("⚠️ Telegram custom emoji iconų nepriėmė — palieku ◆ / ✦ simbolius buttonuose.")
        kwargs["reply_markup"] = keyboard(bot_url, custom=False)
        msg = await bot.send_photo(**kwargs)

    print(f"✅ {kind}: išsiųsta į {CHANNEL_CHAT} · message_id={msg.message_id}")
    if pin:
        try:
            await bot.pin_chat_message(
                chat_id=CHANNEL_CHAT,
                message_id=msg.message_id,
                disable_notification=True,
            )
            print(f"📌 {kind}: prisegta")
        except BadRequest as exc:
            print(f"⚠️ Nepavyko prisegti: {exc}")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("kind", choices=["promo", "contest", "both"])
    parser.add_argument("--pin", action="store_true")
    args = parser.parse_args()

    async with Bot(BOT_TOKEN) as bot:
        kinds = ["promo", "contest"] if args.kind == "both" else [args.kind]
        for kind in kinds:
            # If both are requested, pin only the last post.
            do_pin = args.pin and (len(kinds) == 1 or kind == kinds[-1])
            await send_one(bot, kind, do_pin)


if __name__ == "__main__":
    asyncio.run(main())
