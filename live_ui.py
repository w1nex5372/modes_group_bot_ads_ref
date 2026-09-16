"""NERA DROPO inline UI with custom icons and Telegram's default button style.

Button colours are deliberately not overridden. The existing emoji IDs,
callback actions and target URLs are preserved.
"""

import branded_bot as bb
import referral_bot as rb
from telegram import InlineKeyboardButton, InlineKeyboardMarkup


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
            button("INVITE TOP", key="trophy", callback_data="top"),
            button("VISO TOP", key="stats", callback_data="alltime"),
        ],
        [
            button("PRAEITA SAVAITĖ", key="crown", callback_data="lastweek"),
            button("KAIP VEIKIA", key="brand", callback_data="info"),
        ],
    ]
    if is_admin:
        rows.append([
            button("ADMIN PANEL", key="crown", callback_data="admin_panel")
        ])
    return InlineKeyboardMarkup(rows)


def admin_menu():
    return InlineKeyboardMarkup([
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
        [
            button("TAŠKŲ VALDYMAS", key="add", callback_data="admin_points_help"),
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
            url=rb.share_url(link),
        )
    ]]
    if rb.GROUP_PUBLIC_URL:
        rows.append([
            button(
                "ATIDARYTI NĖRA DROPO",
                key="brand",
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
                url=url,
            )
        ])
    if rb.GROUP_PUBLIC_URL:
        rows.append([
            button(
                "NĖRA DROPO",
                key="brand",
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
