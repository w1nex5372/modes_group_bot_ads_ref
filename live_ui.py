"""Final NERA DROPO UI layer.

Adds custom-emoji icons + styles to inline buttons (Bot API 9.4+) and then
starts branded_bot. Requires python-telegram-bot 22.7+ and a bot owner with
Telegram Premium (or a bot with eligible Fragment usernames) for custom button icons.
"""

import branded_bot as bb
import referral_bot as rb
from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def icon_id(key):
    value = str(rb.EMOJI_IDS.get(key, "")).strip()
    return value or None


def button(text, *, key=None, style=None, **kwargs):
    return InlineKeyboardButton(
        text=text,
        icon_custom_emoji_id=icon_id(key) if key else None,
        style=style,
        **kwargs,
    )


def user_menu(is_admin=False):
    rows = [
        [
            button("MANO INVITE", key="invite", style="primary", callback_data="my_link"),
            button("MANO TAŠKAI", key="trophy", style="success", callback_data="points"),
        ],
        [
            button("INVITE TOP", key="trophy", style="primary", callback_data="top"),
            button("VISO TOP", key="stats", style="primary", callback_data="alltime"),
        ],
        [
            button("PRAEITA SAVAITĖ", key="crown", callback_data="lastweek"),
            button("KAIP VEIKIA", key="brand", callback_data="info"),
        ],
    ]
    if is_admin:
        rows.append([
            button("ADMIN PANEL", key="crown", style="danger", callback_data="admin_panel")
        ])
    return InlineKeyboardMarkup(rows)


def admin_menu():
    return InlineKeyboardMarkup([
        [
            button("ADS STATUS", key="share", style="primary", callback_data="admin_ads_status"),
            button("ADS DABAR", key="share", style="success", callback_data="admin_ads_next"),
        ],
        [
            button("ADS ON", key="add", style="success", callback_data="admin_ads_on"),
            button("ADS OFF", key="crown", style="danger", callback_data="admin_ads_off"),
        ],
        [
            button("REFRESH TOP", key="trophy", style="primary", callback_data="admin_live_refresh"),
            button("STATISTIKA", key="stats", style="primary", callback_data="admin_stats"),
        ],
        [
            button("TAŠKŲ VALDYMAS", key="add", style="success", callback_data="admin_points_help"),
        ],
        [
            button("ADS PREVIEW", key="brand", callback_data="admin_adpack"),
            button("KOMANDOS", key="crown", callback_data="admin_commands"),
        ],
        [button("ATGAL", key="brand", callback_data="back")],
    ])


def invite_markup(link):
    rows = [[
        button(
            "DALINTIS SU DRAUGAIS / GRUPĖMIS",
            key="share",
            style="success",
            url=rb.share_url(link),
        )
    ]]
    if rb.GROUP_PUBLIC_URL:
        rows.append([
            button(
                "ATIDARYTI NĖRA DROPO",
                key="brand",
                style="primary",
                url=rb.GROUP_PUBLIC_URL,
            )
        ])
    return InlineKeyboardMarkup(rows)


def live_markup(application):
    url = rb.bot_url(application, "invite")
    rows = []
    if url:
        rows.append([
            button(
                "DALYVAUTI / GAUTI INVITE",
                key="invite",
                style="success",
                url=url,
            )
        ])
    if rb.GROUP_PUBLIC_URL:
        rows.append([
            button(
                "NĖRA DROPO",
                key="brand",
                style="primary",
                url=rb.GROUP_PUBLIC_URL,
            )
        ])
    return InlineKeyboardMarkup(rows) if rows else None


def native_ad_markup(application):
    return live_markup(application)


# Patch both the core module and the branded layer before startup.
rb.user_menu = user_menu
rb.admin_menu = admin_menu
rb.invite_markup = invite_markup
rb.live_markup = live_markup
rb.native_ad_markup = native_ad_markup

bb.user_menu = user_menu
bb.admin_menu = admin_menu


if __name__ == "__main__":
    bb.main()
