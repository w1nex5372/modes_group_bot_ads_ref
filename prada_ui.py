"""PRADA LUX community theme for the invite/TOP system.

This is an original, unofficial community presentation layer. It intentionally
avoids claiming official affiliation with Prada S.p.A. while keeping a restrained
black/ivory/silver luxury aesthetic.
"""

import json
import os
from contextlib import closing
from urllib.parse import quote

import branded_bot as bb
import referral_bot as rb
from telegram import BotCommand, BotCommandScopeChatAdministrators, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode

BRAND_NAME = os.getenv("PRADA_BRAND_NAME", "PRADA LUX").strip() or "PRADA LUX"
GROUP_LABEL = os.getenv("PRADA_GROUP_LABEL", "PRADA LUX CLUB").strip() or "PRADA LUX CLUB"
DISCLAIMER = os.getenv(
    "PRADA_DISCLAIMER",
    "Neoficiali luxury fashion bendruomenė · nesusijusi su Prada S.p.A.",
).strip()
ASSETS = rb.ASSETS_DIR

# Elegant Unicode fallbacks if a custom-emoji pack has not been created yet.
rb.FALLBACK_EMOJI.update(
    {
        "brand": "◆",
        "crown": "◇",
        "invite": "🔗",
        "share": "↗️",
        "trophy": "🏆",
        "group": "♟",
        "add": "＋",
        "stats": "▥",
    }
)


def brand_line():
    return f"{rb.em('brand')} <b>{rb.esc(BRAND_NAME)}</b>"


def share_url(invite_link: str):
    text = f"Prisijunk prie {GROUP_LABEL} · luxury fashion community"
    return f"https://t.me/share/url?url={quote(invite_link, safe='')}&text={quote(text, safe='')}"


def icon_id(key):
    value = str(rb.EMOJI_IDS.get(key, "")).strip()
    return value or None


def button(text, *, key=None, **kwargs):
    return InlineKeyboardButton(
        text=text,
        icon_custom_emoji_id=icon_id(key) if key else None,
        **kwargs,
    )


def user_menu(is_admin=False):
    rows = [
        [
            button("MANO INVITE", key="invite", callback_data="my_link"),
            button("MANO TAŠKAI", key="trophy", callback_data="points"),
        ],
        [
            button("SAVAITĖS TOP", key="trophy", callback_data="top"),
            button("VISO TOP", key="stats", callback_data="alltime"),
        ],
        [
            button("PRAEITA SAVAITĖ", key="crown", callback_data="lastweek"),
            button("KAIP VEIKIA", key="brand", callback_data="info"),
        ],
    ]
    if is_admin:
        rows.append([button("ADMIN PANEL", key="crown", callback_data="admin_panel")])
    return InlineKeyboardMarkup(rows)


def admin_menu():
    return InlineKeyboardMarkup(
        [
            [
                button("ADS STATUS", key="share", callback_data="admin_ads_status"),
                button("ADS DABAR", key="share", callback_data="admin_ads_next"),
            ],
            [
                button("ADS ON", key="add", callback_data="admin_ads_on"),
                button("ADS OFF", key="crown", callback_data="admin_ads_off"),
            ],
            [
                button("REFRESH TOP", key="trophy", callback_data="admin_live_refresh"),
                button("STATISTIKA", key="stats", callback_data="admin_stats"),
            ],
            [button("TAŠKŲ VALDYMAS", key="add", callback_data="admin_points_help")],
            [
                button("ADS PREVIEW", key="brand", callback_data="admin_adpack"),
                button("KOMANDOS", key="crown", callback_data="admin_commands"),
            ],
            [button("ATGAL", key="brand", callback_data="back")],
        ]
    )


def invite_markup(link):
    rows = [
        [
            button(
                "DALINTIS INVITE",
                key="share",
                url=share_url(link),
            )
        ]
    ]
    if rb.GROUP_PUBLIC_URL:
        rows.append([button("ATIDARYTI GRUPĘ", key="brand", url=rb.GROUP_PUBLIC_URL)])
    return InlineKeyboardMarkup(rows)


def live_markup(application):
    url = rb.bot_url(application, "invite")
    rows = []
    if url:
        rows.append([button("GAUTI MANO INVITE", key="invite", url=url)])
    if rb.GROUP_PUBLIC_URL:
        rows.append([button(GROUP_LABEL, key="brand", url=rb.GROUP_PUBLIC_URL)])
    return InlineKeyboardMarkup(rows) if rows else None


def native_ad_markup(application):
    return live_markup(application)


def home_text():
    return (
        f"{brand_line()}\n"
        f"<b>{rb.esc(GROUP_LABEL)}</b> · Milano mood\n\n"
        f"{rb.em('group')} Luxury fashion, stilius ir bendruomenė\n"
        f"{rb.em('invite')} Naujas narys per tavo invite = <b>+1</b>\n"
        f"{rb.em('add')} Add Members = <b>+1</b>\n\n"
        f"{rb.em('trophy')} Savaitės TOP atsinaujina automatiškai\n"
        f"{rb.em('stats')} Nauja savaitė: pirmadienį 00:00\n\n"
        f"{rb.em('share')} Pradėk nuo <b>MANO INVITE</b>.\n\n"
        f"<i>{rb.esc(DISCLAIMER)}</i>"
    )


def weekly_top_text(excluded=None):
    rows = rb.weekly_top(exclude_ids=excluded)
    lines = [f"{rb.em('trophy')} <b>PRADA LUX · SAVAITĖS TOP 10</b>", ""]
    lines += rb.ranking_lines(rows, "weekly_points", "tšk.") if rows else [
        f"{rb.em('brand')} TOP dar tuščias — būk pirmas"
    ]
    lines += ["", f"{rb.em('stats')} Reset: pirmadienį 00:00", brand_line()]
    return "\n".join(lines)


def alltime_top_text(excluded=None):
    rows = rb.alltime_top(exclude_ids=excluded)
    lines = [f"{rb.em('stats')} <b>PRADA LUX · VISO LAIKO TOP 10</b>", ""]
    lines += rb.ranking_lines(rows, "points", "tšk.") if rows else [
        f"{rb.em('brand')} TOP dar tuščias"
    ]
    lines += ["", brand_line()]
    return "\n".join(lines)


def lastweek_top_text(excluded=None):
    week_key, rows = rb.last_week_top(exclude_ids=excluded)
    if not rows:
        return (
            f"{rb.em('trophy')} <b>PRAEITA SAVAITĖ</b>\n\n"
            f"{rb.em('brand')} Rezultatų dar nėra.\n\n"
            f"{brand_line()}"
        )
    lines = [f"{rb.em('trophy')} <b>PRAEITA SAVAITĖ · {rb.esc(week_key)}</b>", ""]
    lines += rb.ranking_lines(rows, "points", "tšk.")
    lines += ["", brand_line()]
    return "\n".join(lines)


def live_top_text(excluded=None):
    rows = rb.weekly_top(exclude_ids=excluded)
    lines = [f"{rb.em('trophy')} <b>PRADA LUX · WEEKLY TOP · LIVE</b>", ""]
    lines += rb.ranking_lines(rows, "weekly_points", "tšk.") if rows else [
        f"{rb.em('brand')} Būk pirmas"
    ]
    lines += [
        "",
        f"{rb.em('stats')} Reset: pirmadienį 00:00",
        f"{rb.em('stats')} Atnaujinta {rb.local_now():%H:%M}",
        brand_line(),
    ]
    return "\n".join(lines)


def invite_text(link):
    return (
        f"{rb.em('invite')} <b>TAVO PRIVATE INVITE</b>\n\n"
        f"{rb.esc(link)}\n\n"
        f"{rb.em('trophy')} <b>+1 taškas</b> už kiekvieną naują narį:\n"
        f"{rb.em('share')} žmogus prisijungia per tavo nuorodą\n"
        f"{rb.em('add')} arba pats jį pridedi per <b>Add Members</b>\n\n"
        f"{rb.em('brand')} Tas pats žmogus skaičiuojamas 1 kartą.\n\n"
        f"{brand_line()}"
    )


def contest_rose_caption(application):
    b = rb.bot_url(application, "invite") or "https://t.me/TAVO_BOTO_USERNAME"
    g = rb.GROUP_PUBLIC_URL or "https://t.me/TAVO_PRADA_GRUPE"
    return "\n".join(
        [
            "◆ PRADA LUX · WEEKLY INVITE CLUB",
            "",
            "Pakviesk luxury / fashion bendraminčius ir kilk į TOP.",
            "",
            "🔗 Invite nuoroda → +1 už naują narį",
            "＋ Add Members → +1 už naują narį",
            "🏆 Abu būdai sumuojasi",
            "",
            "▥ TOP atsinaujina automatiškai",
            "◇ Nauja savaitė: pirmadienį 00:00",
            "",
            f"[GAUTI MANO INVITE](buttonurl://{b})",
            f"[{GROUP_LABEL}](buttonurl://{g}:same)",
            "",
            "Neoficiali luxury fashion bendruomenė.",
        ]
    )


def promo_rose_caption(application):
    b = rb.bot_url(application, "invite") or "https://t.me/TAVO_BOTO_USERNAME"
    g = rb.GROUP_PUBLIC_URL or "https://t.me/TAVO_PRADA_GRUPE"
    return "\n".join(
        [
            "◆ PRADA LUX COMMUNITY",
            "",
            "Luxury fashion · stilius · diskusijos · bendruomenė",
            "",
            "◇ Atrinkta estetika ir naujienos",
            "🏆 Savaitės invite TOP",
            "▥ Rezultatai realiu laiku",
            "↗️ Visa svarbiausia info vienoje vietoje",
            "",
            f"[ATIDARYTI BOTĄ](buttonurl://{b})",
            f"[PRISIJUNGTI PRIE GRUPĖS](buttonurl://{g}:same)",
            "",
            "Neoficiali fanų bendruomenė · nesusijusi su Prada S.p.A.",
        ]
    )


def ads_status_text():
    try:
        notes = json.loads(rb.get_setting("ads_notes", "[]"))
    except Exception:
        notes = []
    state = "ON" if rb.get_setting("ads_enabled", "0") == "1" else "OFF"
    return (
        f"{rb.em('share')} <b>PRADA LUX · AUTO ADS</b>\n\n"
        f"{rb.em('brand')} Būsena: <b>{state}</b>\n"
        f"{rb.em('stats')} FAST: kas {rb.esc(rb.get_setting('ads_fast_interval_minutes','15'))} min.\n"
        f"{rb.em('stats')} NORMAL: kas {rb.esc(rb.get_setting('ads_interval_minutes','30'))} min.\n"
        f"{rb.em('invite')} Notes: {rb.esc(', '.join(notes) if notes else '—')}\n"
        f"{rb.em('trophy')} Paskutinis: {rb.esc(rb.get_setting('ads_last_note','—') or '—')}\n"
        f"{rb.em('crown')} Rezultatas: {rb.esc(rb.get_setting('ads_last_result','—') or '—')}\n\n"
        f"{brand_line()}"
    )


async def send_ad_preview(chat_id: int, context):
    contest = ASSETS / "prada_ads" / "contest.webp"
    promo = ASSETS / "prada_ads" / "promo.webp"
    contest_text = (
        f"{rb.em('trophy')} <b>PRADA LUX · WEEKLY INVITE CLUB</b>\n\n"
        f"{rb.em('invite')} Invite = <b>+1</b> už naują narį\n"
        f"{rb.em('add')} Add Members = <b>+1</b>\n"
        f"{rb.em('stats')} Reset: pirmadienį 00:00\n\n"
        f"{brand_line()}"
    )
    promo_text = (
        f"{rb.em('brand')} <b>PRADA LUX COMMUNITY</b>\n\n"
        f"Luxury fashion · stilius · bendruomenė\n"
        f"{rb.em('trophy')} Weekly TOP\n"
        f"{rb.em('stats')} Live rezultatai\n\n"
        f"<i>{rb.esc(DISCLAIMER)}</i>"
    )
    markup = native_ad_markup(context.application)
    if contest.exists():
        await context.bot.send_photo(
            chat_id=chat_id,
            photo=contest.read_bytes(),
            caption=contest_text,
            parse_mode=ParseMode.HTML,
            reply_markup=markup,
        )
    else:
        await context.bot.send_message(chat_id=chat_id, text=contest_text, parse_mode=ParseMode.HTML, reply_markup=markup)
    if promo.exists():
        await context.bot.send_photo(
            chat_id=chat_id,
            photo=promo.read_bytes(),
            caption=promo_text,
            parse_mode=ParseMode.HTML,
            reply_markup=markup,
        )
    else:
        await context.bot.send_message(chat_id=chat_id, text=promo_text, parse_mode=ParseMode.HTML, reply_markup=markup)


async def admin_cmd(update, context):
    if not await rb.require_admin(update, context):
        return
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"{rb.em('crown')} <b>ADMIN PANEL</b>\n\n{brand_line()} · valdymas",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_menu(),
    )


async def set_command_menus(application):
    user_commands = [
        BotCommand("start", f"Atidaryti {BRAND_NAME} meniu"),
        BotCommand("mylink", "Mano invite + Dalintis"),
        BotCommand("points", "Mano taškai"),
        BotCommand("top", "Savaitės invite TOP"),
        BotCommand("alltime", "Viso laiko TOP"),
        BotCommand("lastweek", "Praeita savaitė"),
        BotCommand("how", "Kaip veikia"),
    ]
    await application.bot.set_my_commands(user_commands)
    group_id = application.bot_data.get("group_id")
    if group_id:
        admin_commands = user_commands + [
            BotCommand("admin", "Admin panel"),
            BotCommand("addpoints", "Pridėti taškų"),
            BotCommand("takepoints", "Atimti taškų"),
            BotCommand("setpoints", "Nustatyti taškus"),
            BotCommand("resetpoints", "Nunulinti vartotojo taškus"),
            BotCommand("userinfo", "Vartotojo taškai / ID"),
            BotCommand("pointhelp", "Taškų valdymo pagalba"),
            BotCommand("ads", "ADS status"),
            BotCommand("adsset", "Nustatyti ADS notes"),
            BotCommand("adsfast", "FAST intervalas"),
            BotCommand("adsinterval", "NORMAL intervalas"),
            BotCommand("adson", "ADS ON"),
            BotCommand("adsoff", "ADS OFF"),
            BotCommand("adsnext", "ADS dabar"),
            BotCommand("adpack", "ADS preview"),
            BotCommand("stats", "Statistika"),
        ]
        try:
            await application.bot.set_my_commands(
                admin_commands,
                scope=BotCommandScopeChatAdministrators(chat_id=group_id),
            )
        except Exception:
            rb.log.exception("Nepavyko nustatyti admin komandų")


# Patch branded_bot globals. Its existing invite/TOP/admin logic remains unchanged.
bb.brand_line = brand_line
bb.home_text = home_text
bb.user_menu = user_menu
bb.admin_menu = admin_menu
bb.weekly_top_text = weekly_top_text
bb.alltime_top_text = alltime_top_text
bb.lastweek_top_text = lastweek_top_text
bb.live_top_text = live_top_text
bb.invite_text = invite_text
bb.contest_rose_caption = contest_rose_caption
bb.promo_rose_caption = promo_rose_caption
bb.ads_status_text = ads_status_text
bb.admin_cmd = admin_cmd
bb.set_command_menus = set_command_menus

# Patch helpers used directly from referral_bot inside branded handlers.
rb.share_url = share_url
rb.user_menu = user_menu
rb.admin_menu = admin_menu
rb.invite_markup = invite_markup
rb.live_markup = live_markup
rb.native_ad_markup = native_ad_markup
rb.send_ad_preview = send_ad_preview
rb.brand_footer = brand_line


if __name__ == "__main__":
    bb.main()
