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
import sqlite3
from html import escape
from pathlib import Path

from dotenv import load_dotenv
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.error import BadRequest

from generate_prada_assets import generate_channel_promo, generate_contest_poster
from community_config import apply_staged_community_config
from extra_buttons import HIDDEN_KEY, SETTING_KEY, buttons_for, parse_buttons, parse_hidden

load_dotenv()
JACKIE_ACTIVE = apply_staged_community_config()

BOT_TOKEN = os.environ["BOT_TOKEN"].strip()
CHANNEL_CHAT = os.getenv("CHANNEL_CHAT", "").strip()
GROUP_PUBLIC_URL = os.getenv("GROUP_PUBLIC_URL", "").strip()
REPS_PUBLIC_URL = os.getenv("REPS_PUBLIC_URL", "https://t.me/REPASPRADO").strip()
BOT_PUBLIC_URL = os.getenv("BOT_PUBLIC_URL", "https://t.me/PRADA_V1_BOT").strip()
ASSETS_DIR = Path(os.getenv("ASSETS_DIR", "assets"))
EMOJI_IDS_FILE = Path(os.getenv("EMOJI_IDS_FILE", "emoji_ids_prada.json"))
DB_PATH = Path(os.getenv("DB_PATH", "prada_referrals.sqlite3"))

if not CHANNEL_CHAT:
    raise SystemExit("❌ .env įrašyk CHANNEL_CHAT=@TAVO_KANALAS arba kanalo -100... ID")
if not GROUP_PUBLIC_URL:
    raise SystemExit("❌ .env įrašyk GROUP_PUBLIC_URL (public @group link arba private invite link).")


def db_setting(key: str, default: str = ""):
    try:
        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            return row[0] if row else default
    except Exception:
        return default


def channel_label(key: str, default: str):
    value = str(db_setting(f"prada_ui_button_{key}", "") or "").strip()
    return value or default


def channel_icon(key: str, fallback_pack_key: str, custom: bool = True):
    if not custom:
        return None
    override = str(db_setting(f"prada_ui_icon_{key}", "") or "").strip()
    if override:
        return override
    value = str(IDS.get(fallback_pack_key, "")).strip()
    return value or None


def emoji_ids():
    if not EMOJI_IDS_FILE.exists():
        return {}
    try:
        data = json.loads(EMOJI_IDS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


IDS = emoji_ids()


def button(text: str, url: str, *, setting_key: str, fallback_pack_key: str, custom: bool = True):
    return InlineKeyboardButton(
        text=text,
        url=url,
        icon_custom_emoji_id=channel_icon(setting_key, fallback_pack_key, custom),
    )


def keyboard(bot_url: str):
    # Channel buttons intentionally use Unicode symbols instead of Telegram
    # custom-emoji icons so they render reliably in broadcast channels.
    hidden = parse_hidden(db_setting(HIDDEN_KEY, "")) if JACKIE_ACTIVE else set()
    first_row = []
    if "channel_group" not in hidden:
        first_row.append(InlineKeyboardButton(
            channel_label("channel_group", "🥋 GRUPĖ") if JACKIE_ACTIVE else "♛ GRUPĖ",
            url=GROUP_PUBLIC_URL,
        ))
    if "channel_invite" not in hidden:
        first_row.append(InlineKeyboardButton(
            channel_label("channel_invite", "🔗 MANO INVITE") if JACKIE_ACTIVE else "✦ MANO INVITE",
            url=bot_url,
        ))
    rows = [first_row] if first_row else []
    if JACKIE_ACTIVE:
        if "channel_bot" not in hidden:
            rows.append([InlineKeyboardButton(channel_label("channel_bot", "🐉 BOTAS"), url=BOT_PUBLIC_URL)])
        for item in buttons_for(parse_buttons(db_setting(SETTING_KEY, "")), "channel"):
            rows.append([InlineKeyboardButton(item["label"], url=item["url"])])
    else:
        rows.append([
            InlineKeyboardButton("♢ REPS", url=REPS_PUBLIC_URL),
            InlineKeyboardButton("🤖 BOTUKAS", url=BOT_PUBLIC_URL),
        ])
    return InlineKeyboardMarkup(rows) if rows else None


def post_data(kind: str):
    if JACKIE_ACTIVE:
        if kind == "info":
            body = str(db_setting("jackie_info_body", "") or "").strip()
            if not body:
                body = "✨ Naujienos · 🏆 Konkursai · 🔥 Savaitės TOP\n\n🔗 Prisijunk prie bendruomenės ir susikurk savo kvietimą ↓"
            caption = (
                "<b>🥋 JACKIE CHAN INFO</b>\n\n"
                f"{escape(body[:700])}\n\n"
                "<i>Neoficiali bendruomenė · nesusijusi su Jackie Chanu.</i>"
            )
            return ASSETS_DIR / "jackie_info.png", caption
        if kind == "contest":
            caption = (
                "<b>🥋 JACKIE CHAN · KVIETIMŲ TOP</b>\n\n"
                "Pakviesk draugą į dojo ir kilk į TOP.\n\n"
                "🔗 Invite nuoroda → <b>+1</b>\n"
                "➕ Add Members → <b>+1</b>\n"
                "🏆 Naujas raundas: pirmadienį 00:00\n\n"
                "<i>Neoficiali bendruomenė · nesusijusi su Jackie Chanu.</i>"
            )
        else:
            caption = (
                "<b>🐉 JACKIE CHAN COMMUNITY</b>\n\n"
                "Kvietimai · taškai · savaitės TOP\n"
                "Prisijunk prie bendruomenės ↓\n\n"
                "<i>Neoficiali bendruomenė · nesusijusi su Jackie Chanu.</i>"
            )
        return ASSETS_DIR / "jackie_shop.png", caption
    if kind == "info":
        raise ValueError("Jackie INFO įrašas galimas tik su aktyviu Jackie botu")
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
            "<b>PRADA INFO</b>\n"
            "Naujienos · Konkursai · Savaitės TOP\n\n"
            "Prisijunk prie bendruomenės ↓\n\n"
            "<i>Neoficiali bendruomenė · nesusijusi su Prada S.p.A.</i>"
        ),
    )


async def send_one(bot: Bot, kind: str, pin: bool):
    # The new theme uses its approved Jackie image; legacy Prada posters stay
    # available only while the legacy bot is active.
    if not JACKIE_ACTIVE:
        if kind == "contest":
            generate_contest_poster()
        else:
            generate_channel_promo()

    me = await bot.get_me()
    if kind == "info":
        # A one-shot pinned announcement must not accidentally land in the
        # legacy channel or post successfully without the ability to pin.
        chat = await bot.get_chat(CHANNEL_CHAT)
        if chat.type != "channel" or "jackie" not in (chat.title or "").casefold():
            raise RuntimeError("INFO target is not the Jackie channel")
        membership = await bot.get_chat_member(chat.id, me.id)
        if membership.status not in {"administrator", "creator"} or not getattr(
            membership, "can_post_messages", False
        ):
            raise RuntimeError("Jackie bot cannot post in the INFO channel")
        if pin and not getattr(membership, "can_edit_messages", False):
            raise RuntimeError("Jackie bot cannot pin in the INFO channel")
    bot_url = f"https://t.me/{me.username}?start=invite"
    image, caption = post_data(kind)
    if not image.exists():
        raise SystemExit(f"❌ Nerastas assetas: {image}")

    kwargs = dict(
        chat_id=CHANNEL_CHAT,
        photo=image.read_bytes(),
        caption=caption,
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard(bot_url),
    )
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
            if kind == "info":
                with sqlite3.connect(DB_PATH) as conn:
                    conn.execute(
                        "INSERT INTO settings(key, value) VALUES (?, ?) "
                        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        ("jackie_info_post_id", str(msg.message_id)),
                    )
        except BadRequest as exc:
            print(f"⚠️ Nepavyko prisegti: {exc}")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("kind", choices=["info", "promo", "contest", "both"])
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
