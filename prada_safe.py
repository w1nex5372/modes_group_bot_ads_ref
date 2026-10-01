"""Safe PRADA LUX runtime wrapper with DB-backed UI editor.

- Message text uses safe Unicode fallbacks.
- Inline buttons can keep custom emoji icons.
- Admin can edit the /start text and button labels from Telegram.
- UI changes are stored in the existing SQLite settings table and survive restarts.
"""

import logging
import json
import os
import time
from contextlib import closing
from pathlib import Path

import prada_ui as p
import branded_bot as bb
import referral_bot as rb
import ads_builder
import top3_ads
from extra_buttons import (
    MAX_PER_PLACE, PLACES, SETTING_KEY, add_button, buttons_for, encode_buttons,
    parse_buttons, remove_button, validate_label, validate_url,
)
from telegram import BotCommand, BotCommandScopeChatAdministrators, InlineKeyboardButton, InlineKeyboardMarkup, InputProfilePhotoStatic
from telegram.constants import ChatType
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ChatMemberHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

# PTB uses httpx; INFO request logs include the Bot API URL (and bot token).
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

_PRADA_EMOJI_IDS = dict(rb.EMOJI_IDS)
_USE_BUTTON_CUSTOM = os.getenv("PRADA_BUTTON_CUSTOM_EMOJI", "1").strip().lower() not in {
    "0", "false", "no", "off",
}
_DEFAULT_HOME_TEXT = p.home_text

BUTTON_DEFAULTS = {
    "invite": "MANO INVITE",
    "points": "MANO TAŠKAI",
    "top": "SAVAITĖS TOP",
    "alltime": "VISO TOP",
    "lastweek": "PRAEITA SAVAITĖ",
    "info": "KAIP VEIKIA",
    "admin": "ADMIN PANEL",
    "share": "DALINTIS INVITE",
    "open_group": "ATIDARYTI GRUPĘ",
    "live_invite": "GAUTI MANO INVITE",
    "live_group": p.GROUP_LABEL,
    "channel_group": "🥋 GRUPĖ" if p.JACKIE_THEME else "GRUPĖ",
    "channel_invite": "🔗 MANO INVITE" if p.JACKIE_THEME else "MANO INVITE",
    "channel_bot": "🐉 BOTAS",
}
BUTTON_KEYS = tuple(BUTTON_DEFAULTS)


def safe_icon_id(key):
    if not _USE_BUTTON_CUSTOM:
        return None
    value = _PRADA_EMOJI_IDS.get(key)
    return str(value).strip() if value else None


p.icon_id = safe_icon_id

# Custom emoji entities in message text must wrap the exact Unicode fallback
# used when the custom-emoji sticker was created. Using decorative symbols
# like ◆/◇ here makes Telegram reject the entity as Entity_text_invalid.
rb.FALLBACK_EMOJI.update({
    "brand": "🥋" if p.JACKIE_THEME else "🖤",
    "crown": "🐉" if p.JACKIE_THEME else "💎",
    "invite": "🔗",
    "share": "📤",
    "trophy": "🏆",
    "group": "🎬" if p.JACKIE_THEME else "👥",
    "add": "➕",
    "stats": "📈",
})


def setting(key, default=""):
    try:
        return rb.get_setting(key, default)
    except Exception:
        return default


def button_label(key):
    value = str(setting(f"prada_ui_button_{key}", "") or "").strip()
    return value or BUTTON_DEFAULTS[key]


def button_icon_id(key, fallback_key=None):
    """Return a DB-selected custom emoji icon, otherwise the pack default."""
    if not _USE_BUTTON_CUSTOM:
        return None
    custom = str(setting(f"prada_ui_icon_{key}", "") or "").strip()
    if custom:
        return custom
    return safe_icon_id(fallback_key or key)


def dynamic_button(key, *, fallback_icon=None, text=None, **kwargs):
    return InlineKeyboardButton(
        text=text if text is not None else button_label(key),
        icon_custom_emoji_id=button_icon_id(key, fallback_icon),
        **kwargs,
    )


def custom_buttons():
    try:
        return parse_buttons(setting(SETTING_KEY, ""))
    except ValueError:
        rb.log.exception("Papildomų Jackie mygtukų duomenys netinkami")
        return []


def custom_rows(place):
    if not p.JACKIE_THEME:
        return []
    return [
        [InlineKeyboardButton(item["label"], url=item["url"])]
        for item in buttons_for(custom_buttons(), place)
    ]


def change_custom_buttons(transform):
    """Serialize admin changes so concurrent callbacks cannot lose a button."""
    with closing(rb.db()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT value FROM settings WHERE key=?", (SETTING_KEY,)).fetchone()
        current = parse_buttons(row["value"] if row else "")
        updated = transform(current)
        conn.execute(
            "INSERT INTO settings(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (SETTING_KEY, encode_buttons(updated)),
        )
        conn.commit()
    return updated


def dynamic_home_text():
    custom_html = str(setting("prada_ui_home_html", "") or "").strip()
    if custom_html:
        return custom_html
    custom = str(setting("prada_ui_home_text", "") or "").strip()
    if custom:
        return rb.esc(custom)
    return _DEFAULT_HOME_TEXT()


def dynamic_user_menu(is_admin=False):
    rows = [
        [
            dynamic_button("invite", fallback_icon="invite", callback_data="my_link"),
            dynamic_button("points", fallback_icon="trophy", callback_data="points"),
        ],
        [
            dynamic_button("top", fallback_icon="trophy", callback_data="top"),
            dynamic_button("alltime", fallback_icon="stats", callback_data="alltime"),
        ],
        [
            dynamic_button("lastweek", fallback_icon="crown", callback_data="lastweek"),
            dynamic_button("info", fallback_icon="brand", callback_data="info"),
        ],
    ]
    if is_admin:
        rows.append([dynamic_button("admin", fallback_icon="crown", callback_data="admin_panel")])
    rows.extend(custom_rows("home"))
    return InlineKeyboardMarkup(rows)


def dynamic_admin_menu():
    rows = [
        [p.button("📣 REKLAMŲ VALDYMAS", key="share", callback_data="adsb_home")],
        [
            p.button("REFRESH TOP", key="trophy", callback_data="admin_live_refresh"),
            p.button("STATISTIKA", key="stats", callback_data="admin_stats"),
        ],
        [p.button("TAŠKŲ VALDYMAS", key="add", callback_data="admin_points_help")],
        [p.button("UI REDAGUOTI", key="brand", callback_data="admin_ui")],
        [p.button("KOMANDOS", key="crown", callback_data="admin_commands")],
    ]
    rows.extend(custom_rows("admin"))
    rows.append([p.button("ATGAL", key="brand", callback_data="back")])
    return InlineKeyboardMarkup(rows)


def dynamic_invite_markup(link):
    rows = [[dynamic_button("share", fallback_icon="share", url=p.share_url(link))]]
    if rb.GROUP_PUBLIC_URL:
        rows.append([dynamic_button("open_group", fallback_icon="brand", url=rb.GROUP_PUBLIC_URL)])
    rows.extend(custom_rows("invite"))
    return InlineKeyboardMarkup(rows)


def dynamic_live_markup(application):
    url = rb.bot_url(application, "invite")
    rows = []
    if url:
        rows.append([dynamic_button("live_invite", fallback_icon="invite", url=url)])
    if rb.GROUP_PUBLIC_URL:
        rows.append([dynamic_button("live_group", fallback_icon="brand", url=rb.GROUP_PUBLIC_URL)])
    rows.extend(custom_rows("live"))
    return InlineKeyboardMarkup(rows) if rows else None


async def refresh_channel_info_post(bot):
    """Edit only the verified Jackie INFO post currently pinned in our channel."""
    if not p.JACKIE_THEME:
        raise RuntimeError("Jackie INFO editor is unavailable in the legacy theme")
    import post_prada_channel as channel

    chat = await bot.get_chat(channel.CHANNEL_CHAT)
    pinned = getattr(chat, "pinned_message", None)
    if chat.type != "channel" or "jackie" not in (chat.title or "").casefold():
        raise RuntimeError("Wrong INFO channel")
    if not pinned or "JACKIE CHAN INFO" not in (pinned.caption or ""):
        raise RuntimeError("Jackie INFO post is not pinned")
    recorded_id = str(setting("jackie_info_post_id", "") or "").strip()
    if recorded_id and recorded_id != str(pinned.message_id):
        raise RuntimeError("Pinned INFO post differs from the recorded post")
    me = await bot.get_me()
    markup = channel.keyboard(f"https://t.me/{me.username}?start=invite")
    caption = channel.post_data("info")[1]
    try:
        await bot.edit_message_caption(
            chat_id=chat.id,
            message_id=pinned.message_id,
            caption=caption,
            parse_mode="HTML",
            reply_markup=markup,
        )
    except BadRequest as exc:
        if "message is not modified" not in str(exc).casefold():
            raise
    if markup is None:
        try:
            await bot.edit_message_reply_markup(
                chat_id=chat.id, message_id=pinned.message_id, reply_markup=None,
            )
        except BadRequest as exc:
            if "message is not modified" not in str(exc).casefold():
                raise
    if not recorded_id:
        rb.set_setting("jackie_info_post_id", str(pinned.message_id))
    return pinned.message_id


async def channel_info_refresh_result(bot):
    try:
        await refresh_channel_info_post(bot)
    except Exception:
        rb.log.exception("Nepavyko atnaujinti Jackie INFO kanalo įrašo")
        return "⚠️ Išsaugota DB, bet prisegto INFO įrašo atnaujinti nepavyko. Patikrink kanalo teises."
    return "✅ Prisegtas INFO įrašas atnaujintas."


def ui_editor_menu():
    rows = [
        [p.button("REDAGUOTI TEKSTĄ", key="brand", callback_data="ui_home")],
        [p.button("REDAGUOTI MYGTUKUS", key="stats", callback_data="ui_buttons")],
    ]
    if p.JACKIE_THEME:
        rows.append([p.button("➕ PRIDĖTI MYGTUKĄ", key="add", callback_data="ui_add")])
        rows.append([p.button("PAPILDOMI MYGTUKAI", key="stats", callback_data="ui_extras")])
        rows.append([p.button("INFO KANALO TEKSTAS", key="brand", callback_data="ui_channel_caption")])
    rows.extend([
        [p.button("ATSTATYTI DEFAULT", key="crown", callback_data="ui_reset")],
        [p.button("ATGAL Į ADMIN", key="brand", callback_data="ui_back_admin")],
    ])
    return InlineKeyboardMarkup(rows)


def ui_buttons_menu():
    rows = []
    pairs = [
        ("invite", "points"),
        ("top", "alltime"),
        ("lastweek", "info"),
        ("admin", "share"),
        ("open_group", "live_invite"),
        ("live_group", None),
    ]
    for left, right in pairs:
        row = [p.button(button_label(left), key="brand", callback_data=f"ui_btn_{left}")]
        if right:
            row.append(p.button(button_label(right), key="brand", callback_data=f"ui_btn_{right}"))
        rows.append(row)
    rows.append([
        p.button(f"CHANNEL: {button_label('channel_group')}", key="group", callback_data="ui_btn_channel_group"),
        p.button(f"CHANNEL: {button_label('channel_invite')}", key="invite", callback_data="ui_btn_channel_invite"),
    ])
    if p.JACKIE_THEME:
        rows.append([p.button(f"CHANNEL: {button_label('channel_bot')}", key="brand", callback_data="ui_btn_channel_bot")])
        rows.append([p.button("➕ PRIDĖTI NAUJĄ", key="add", callback_data="ui_add")])
        rows.append([p.button("PAPILDOMI MYGTUKAI", key="stats", callback_data="ui_extras")])
    rows.append([p.button("ATGAL", key="brand", callback_data="admin_ui")])
    return InlineKeyboardMarkup(rows)


def ui_add_menu():
    rows = [[p.button(name, key="add", callback_data=f"ui_add_{place}")]
            for place, name in PLACES.items()]
    rows.append([p.button("ATGAL", key="brand", callback_data="admin_ui")])
    return InlineKeyboardMarkup(rows)


def ui_extras_menu():
    rows = [
        [p.button(f"{PLACES[item['place']]} · {item['label'][:30]}", key="brand",
                  callback_data=f"ui_extra_{item['id']}")]
        for item in custom_buttons()
    ]
    rows.append([p.button("➕ PRIDĖTI", key="add", callback_data="ui_add")])
    rows.append([p.button("ATGAL", key="brand", callback_data="admin_ui")])
    return InlineKeyboardMarkup(rows)


def ui_button_edit_menu(key):
    rows = [[p.button("KEISTI TEKSTĄ", key="brand", callback_data=f"ui_btntext_{key}")]]
    if not key.startswith("channel_"):
        rows.extend([
            [p.button("NUSTATYTI CUSTOM EMOJI", key="crown", callback_data=f"ui_btnemoji_{key}")],
            [p.button("NUIMTI CUSTOM EMOJI", key="brand", callback_data=f"ui_btnclearicon_{key}")],
        ])
    rows.append([p.button("ATGAL", key="brand", callback_data="ui_buttons")])
    return InlineKeyboardMarkup(rows)


async def refresh_custom_surface(place, context):
    if place == "channel":
        return "\n" + await channel_info_refresh_result(context.bot)
    if place == "live":
        try:
            await rb.refresh_live_leaderboard(context.application)
        except Exception:
            rb.log.exception("Nepavyko atnaujinti LIVE mygtukų")
            return "\n⚠️ Išsaugota, bet esamo LIVE įrašo atnaujinti nepavyko."
        return "\n✅ Esamas LIVE įrašas atnaujintas, jei jis aktyvus."
    return "\nNaujas mygtukas matysis naujai siunčiamuose pranešimuose."


async def show_ui_editor(update, context, edit=False):
    text = (
        f"<b>{rb.esc(p.BRAND_NAME)} · UI REDAKTORIUS</b>\n\n"
        "Pakeitimai saugomi SQLite DB ir lieka po restart.\n"
        "Gali keisti /start tekstą, esamus mygtukus bei jų ikonas ir pridėti URL mygtukus."
    )
    if edit and update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode="HTML", reply_markup=ui_editor_menu())
    else:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=text, parse_mode="HTML", reply_markup=ui_editor_menu())


async def uiedit_cmd(update, context):
    if not await rb.require_admin(update, context):
        return
    await show_ui_editor(update, context)


async def sethome_cmd(update, context):
    if not await rb.require_admin(update, context):
        return
    reply = getattr(update.effective_message, "reply_to_message", None)
    value = None
    value_html = None
    if reply:
        value = reply.text or reply.caption
        value_html = reply.text_html or reply.caption_html
    elif context.args:
        value = " ".join(context.args).replace("\\n", "\n")
        value_html = rb.esc(value)
    if not value:
        context.user_data["prada_ui_edit"] = "home"
        await update.effective_message.reply_text(
            "Atsiųsk naują /start tekstą viena žinute. Premium custom emoji bus išsaugoti kartu su tekstu."
        )
        return
    rb.set_setting("prada_ui_home_text", value[:4000])
    rb.set_setting("prada_ui_home_html", (value_html or rb.esc(value))[:8000])
    await update.effective_message.reply_text("✅ /start tekstas + custom emoji išsaugoti DB.")


async def setbtn_cmd(update, context):
    if not await rb.require_admin(update, context):
        return
    if len(context.args) < 2:
        await update.effective_message.reply_text(
            "Naudojimas: /setbtn KEY NAUJAS PAVADINIMAS\n\nKEY: " + ", ".join(BUTTON_KEYS)
        )
        return
    key = context.args[0].lower()
    if key not in BUTTON_DEFAULTS:
        await update.effective_message.reply_text("❌ Nežinomas KEY. Galimi: " + ", ".join(BUTTON_KEYS))
        return
    label = " ".join(context.args[1:]).strip()
    if not 1 <= len(label) <= 64:
        await update.effective_message.reply_text("❌ Pavadinimas turi būti 1–64 simbolių.")
        return
    rb.set_setting(f"prada_ui_button_{key}", label)
    channel_result = (
        "\n" + await channel_info_refresh_result(context.bot)
        if key.startswith("channel_") and p.JACKIE_THEME else ""
    )
    await update.effective_message.reply_text(f"✅ Mygtukas {key} → {label}{channel_result}")
    try:
        await rb.refresh_live_leaderboard(context.application)
    except Exception:
        pass


async def setbtnicon_cmd(update, context):
    if not await rb.require_admin(update, context):
        return
    if not context.args:
        await update.effective_message.reply_text(
            "Naudojimas: reply į žinutę su custom emoji → /setbtnicon KEY\n\nKEY: "
            + ", ".join(BUTTON_KEYS)
        )
        return
    key = context.args[0].lower()
    if key not in BUTTON_DEFAULTS:
        await update.effective_message.reply_text("❌ Nežinomas KEY. Galimi: " + ", ".join(BUTTON_KEYS))
        return
    if key.startswith("channel_"):
        await update.effective_message.reply_text("Kanalo mygtukams naudok paprastą emoji tekste per /setbtn.")
        return
    reply = getattr(update.effective_message, "reply_to_message", None)
    entities = list(getattr(reply, "entities", None) or []) if reply else []
    custom = next(
        (
            getattr(entity, "custom_emoji_id", None)
            for entity in entities
            if getattr(entity, "type", None) == "custom_emoji"
            and getattr(entity, "custom_emoji_id", None)
        ),
        None,
    )
    if not custom:
        await update.effective_message.reply_text("❌ Reply turi būti į žinutę su Telegram Premium custom emoji.")
        return
    rb.set_setting(f"prada_ui_icon_{key}", str(custom))
    await update.effective_message.reply_text(f"✅ Custom emoji išsaugotas: {key}")


async def resetui_cmd(update, context):
    if not await rb.require_admin(update, context):
        return
    rb.set_setting("prada_ui_home_text", "")
    rb.set_setting("prada_ui_home_html", "")
    for key in BUTTON_KEYS:
        rb.set_setting(f"prada_ui_button_{key}", "")
        rb.set_setting(f"prada_ui_icon_{key}", "")
    if p.JACKIE_THEME:
        rb.set_setting("jackie_info_body", "")
        rb.set_setting(SETTING_KEY, "[]")
    context.user_data.pop("prada_ui_edit", None)
    channel_result = "\n" + await channel_info_refresh_result(context.bot) if p.JACKIE_THEME else ""
    await update.effective_message.reply_text("✅ UI grąžintas į default." + channel_result)
    try:
        await rb.refresh_live_leaderboard(context.application)
    except Exception:
        pass


def ads_builder_menu():
    return InlineKeyboardMarkup([
        [p.button("✏️ ĮRAŠYTI PAVADINIMĄ", key="add", callback_data="adsb_name_custom")],
        [p.button("📚 VISOS REKLAMOS", key="brand", callback_data="adsb_list_0")],
        [p.button("⬅️ ATGAL", key="brand", callback_data="adsb_cancel")],
    ])


def ads_builder_confirm_menu(button_count=0):
    return InlineKeyboardMarkup([
        [p.button(f"🔗 MYGTUKAI ({button_count})", key="share", callback_data="adsb_buttons")],
        [p.button("💾 IŠSAUGOTI BOTE", key="add", callback_data="adsb_save")],
        [p.button("📣 IŠSAUGOTI IR SIŲSTI", key="share", callback_data="adsb_launch")],
        [p.button("⬅️ ATGAL", key="brand", callback_data="adsb_cancel")],
    ])


def ads_builder_back_menu():
    return InlineKeyboardMarkup([[p.button("⬅️ ATGAL", key="brand", callback_data="adsb_back")]])


def ads_builder_selected():
    try:
        names = json.loads(setting("ads_builder_selected_notes", "[]"))
    except (ValueError, TypeError):
        names = []
    return [name for name in names if isinstance(name, str) and ads_builder.NAME_RE.fullmatch(name)]


def ads_builder_save_selected(names):
    rb.set_setting("ads_builder_selected_notes", json.dumps(list(dict.fromkeys(names)), ensure_ascii=False))


def ads_builder_known_names():
    names = []
    for key in ("ads_builder_known_notes", "ads_notes"):
        try:
            values = json.loads(setting(key, "[]"))
        except (ValueError, TypeError):
            values = []
        if isinstance(values, list):
            for name in values:
                if isinstance(name, str) and ads_builder.NAME_RE.fullmatch(name) and name not in names:
                    names.append(name)
    rb.set_setting("ads_builder_known_notes", json.dumps(names, ensure_ascii=False))
    return names


def ads_builder_active_selection():
    """Only saved, current ads may enter the native rotation."""
    active = {row["note"] for row in ads_builder.active_ads(rb.DB_PATH)}
    selected = ads_builder_selected()
    return [name for name in selected if name in active]


def ads_builder_running_notes():
    try:
        names = json.loads(setting("ads_notes", "[]"))
    except (TypeError, ValueError):
        return []
    return [name for name in names if isinstance(name, str)] if isinstance(names, list) else []


def ads_builder_start_rotation():
    selected = ads_builder_active_selection()
    if not selected:
        return False
    rb.set_setting("ads_notes", json.dumps(selected, ensure_ascii=False))
    rb.set_setting("ads_scheduler_initialized", "0")
    rb.set_setting("ads_last_result", "waiting")
    rb.set_setting("ads_last_error", "")
    rb.set_setting("ads_enabled", "1")
    return True


def ads_builder_stop_rotation():
    rb.set_setting("ads_enabled", "0")
    rb.set_setting("ads_force_send", "0")


def ads_builder_dashboard_payload(notice=""):
    ads_builder.init(rb.DB_PATH)
    managed = ads_builder.active_ads(rb.DB_PATH)
    selected = ads_builder_active_selection()
    enabled = setting("ads_enabled", "0") == "1"
    running = ads_builder_running_notes()
    state = "🟢 ON" if enabled and running else "🔴 OFF"
    if enabled and not running:
        state = "⚠️ ON be reklamos — sustabdyti"
    top3 = "ON" if setting("ads_top3_enabled", "0") == "1" else "OFF"
    result = setting("ads_last_result", "never") or "never"
    error = setting("ads_last_error", "") or ""
    lines = [
        "📣 <b>REKLAMŲ VALDYMAS</b>", "",
        f"Reklamų AUTO: <b>{state}</b>",
        f"Patvirtintų reklamų: {len(managed)} · pasirinkta: {len(selected)}",
        f"Rotacijoje: {rb.esc(', '.join(running) or '—')}",
        f"Paskutinis rezultatas: {rb.esc(result)}",
        f"Savaitės TOP 3: {top3}",
    ]
    if error:
        lines.append(f"⚠️ {rb.esc(error[:140])}")
    if notice:
        lines.extend(["", rb.esc(notice)])
    rows = [
        [p.button("📚 VISOS REKLAMOS", key="brand", callback_data="adsb_list_0")],
        [p.button("➕ NAUJA REKLAMA", key="add", callback_data="adsb_start")],
        [p.button("▶️ ĮJUNGTI AUTO", key="share", callback_data="adsb_auto_start"),
         p.button("⏸ SUSTABDYTI", key="brand", callback_data="adsb_auto_stop")],
        [p.button("⏱ INTERVALAI", key="stats", callback_data="adsb_intervals"),
         p.button("🏆 TOP 3", key="trophy", callback_data="adsb_top3")],
        [p.button("⬅️ ADMIN PANEL", key="brand", callback_data="admin_panel")],
    ]
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def ads_builder_dashboard(q, notice=""):
    text_value, markup = ads_builder_dashboard_payload(notice)
    await q.edit_message_text(text_value, parse_mode="HTML", reply_markup=markup)


def ads_builder_links(buttons):
    if not buttons:
        return None
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            button["label"], url=button["url"],
            icon_custom_emoji_id=button.get("icon_custom_emoji_id"),
        )] for button in buttons
    ])


def ads_button_label_with_icon(message):
    """A Telegram custom emoji in a label becomes the URL button's icon."""
    label = message.text or ""
    custom = [entity for entity in (getattr(message, "entities", None) or [])
              if entity.type == "custom_emoji"]
    if len(custom) > 1:
        raise ValueError("URL mygtukas gali turėti tik vieną custom emoji")
    if not custom:
        return label.strip(), ""
    entity = custom[0]
    encoded = label.encode("utf-16-le")
    start, end = entity.offset * 2, (entity.offset + entity.length) * 2
    label = (encoded[:start] + encoded[end:]).decode("utf-16-le").strip()
    return label, str(entity.custom_emoji_id or "")


def ads_builder_draft_from_row(row, step="confirm"):
    return {
        "step": step, "note": row["note"], "kind": row["kind"], "body": row["body"],
        "source_message_id": row["source_message_id"],
        "source_chat_id": row["source_chat_id"] or row["admin_id"],
        "media_file_id": row.get("media_file_id", ""),
        "entities": json.loads(row.get("entities_json", "[]")),
        "buttons": json.loads(row["buttons_json"]),
    }


async def ads_builder_preview(update, context, draft):
    msg = update.effective_message
    buttons = draft.get("buttons", [])
    ads_builder.validate_direct_size(draft["kind"], draft["body"], buttons)
    await msg.reply_text(f"👀 Peržiūra · {draft['note']}:")
    markup = ads_builder_links(buttons)
    entities = ads_builder.message_entities(json.dumps(draft.get("entities", [])))
    if draft["kind"] == "text":
        await context.bot.send_message(
            chat_id=update.effective_chat.id, text=draft["body"], reply_markup=markup,
            entities=entities or None,
        )
    elif draft.get("media_file_id"):
        method = context.bot.send_video if draft["kind"] == "video" else context.bot.send_photo
        media_arg = "video" if draft["kind"] == "video" else "photo"
        await method(
            chat_id=update.effective_chat.id, **{media_arg: draft["media_file_id"]},
            caption=draft["body"], reply_markup=markup, caption_entities=entities or None,
        )
    else:
        await context.bot.copy_message(
            chat_id=update.effective_chat.id,
            from_chat_id=draft["source_chat_id"],
            message_id=draft["source_message_id"],
            caption=draft["body"], reply_markup=markup, caption_entities=entities or None,
        )
    await msg.reply_text(
        "Gali pridėti URL mygtukus arba išsaugoti. „Išsaugoti ir siųsti“ "
        "vieną kartą paskelbs reklamą grupėje tiesiai iš boto, be Rose komandų.",
        reply_markup=ads_builder_confirm_menu(len(buttons)),
    )


async def ads_builder_list(q, page=0):
    ads_builder.init(rb.DB_PATH)
    managed = ads_builder.active_ads(rb.DB_PATH)
    managed_names = {row["note"] for row in managed}
    legacy = [name for name in ads_builder_known_names() if name not in managed_names]
    entries = [("managed", row) for row in managed] + [("legacy", name) for name in legacy]
    selected = ads_builder_selected()
    page_count = max(1, (len(entries) + 7) // 8)
    page = max(0, min(page, page_count - 1))
    rows = []
    top3_state = "ON" if setting("ads_top3_enabled", "0") == "1" else "OFF"
    rows.append([p.button(f"🏆 SAVAITĖS TOP 3 · AUTO {top3_state}", key="trophy", callback_data="adsb_top3")])
    for index in range(page * 8, min((page + 1) * 8, len(entries))):
        kind, value = entries[index]
        if kind == "managed":
            marker = "🔁" if value["note"] in selected else "✅"
            old_media = value["kind"] in {"photo", "video"} and not value.get("media_file_id")
            label = f"{marker} {value['note']} · {value['kind']}{' ⚠️' if old_media else ''}"
            action = f"adsb_view_{value['id']}"
        else:
            label = f"❔ {value} · senas įrašas"
            action = f"adsb_legacy_{index - len(managed)}"
        rows.append([p.button(label[:55], key="brand", callback_data=action)])
    nav = []
    if page:
        nav.append(p.button("◀️", key="brand", callback_data=f"adsb_list_{page - 1}"))
    if page + 1 < page_count:
        nav.append(p.button("▶️", key="brand", callback_data=f"adsb_list_{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([p.button("➕ NAUJAS ADS", key="add", callback_data="adsb_start")])
    rows.append([p.button("⬅️ REKLAMŲ VALDYMAS", key="brand", callback_data="adsb_home")])
    enabled = setting("ads_enabled", "0") == "1"
    await q.edit_message_text(
        f"📚 <b>ESAMI ADS</b> ({len(entries)}) · {page + 1}/{page_count}\n\n"
        f"Reklamų AUTO: {'🟢 ON' if enabled else '🔴 OFF'} · pasirinkta: {rb.esc(', '.join(selected) or '—')}\n"
        "✅ įrašas su turiniu · ❔ senas pavadinimas be išsaugoto turinio\n"
        "🔁 pažymėta rotacijai. Pasirink reklamą peržiūrai, keitimui ar šalinimui.",
        parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows),
    )


async def ads_builder_buttons_screen(q, draft):
    buttons = draft.get("buttons", [])
    rows = []
    for index, item in enumerate(buttons):
        rows.append([
            p.button(f"✏️ {index + 1}. {item['label']}"[:45], key="share", callback_data=f"adsb_be_{index}"),
            p.button("🗑", key="brand", callback_data=f"adsb_bd_{index}"),
        ])
    if len(buttons) < 6:
        rows.append([p.button("➕ PRIDĖTI URL MYGTUKĄ", key="add", callback_data="adsb_ba")])
    rows.append([p.button("👀 PERŽIŪRA / IŠSAUGOTI", key="brand", callback_data="adsb_bdone")])
    rows.append([p.button("⬅️ ATGAL", key="brand", callback_data="adsb_backconfirm")])
    await q.edit_message_text(
        f"🔗 <b>URL MYGTUKAI</b> · {draft['note']}\n"
        f"{len(buttons)}/6\n\n"
        "Paspausk pavadinimą, jei nori pakeisti, arba šiukšliadėžę, jei pašalinti.",
        parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows),
    )


async def ads_builder_extended_button(update, context):
    q = update.callback_query
    data = q.data or ""
    if data == "adsb_home":
        await ads_builder_dashboard(q)
        return True
    if data == "adsb_intervals" or data.startswith("adsb_fast_") or data.startswith("adsb_normal_"):
        if data != "adsb_intervals":
            lane, _, value = data.removeprefix("adsb_").partition("_")
            if value.isdigit() and int(value) in {5, 15, 30, 60, 120}:
                rb.set_setting("ads_fast_interval_minutes" if lane == "fast" else "ads_interval_minutes", value)
                rb.set_setting("ads_scheduler_initialized", "0")
        fast = setting("ads_fast_interval_minutes", "15")
        normal = setting("ads_interval_minutes", "30")
        await q.edit_message_text(
            f"⏱ <b>REKLAMŲ AUTO INTERVALAI</b>\n\n"
            f"FAST: kas {rb.esc(fast)} min. · NORMAL: kas {rb.esc(normal)} min.\n"
            "Naujas intervalas taikomas nuo kito rotacijos ciklo.",
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup([
                [p.button(f"FAST 5", key="brand", callback_data="adsb_fast_5"),
                 p.button("15", key="brand", callback_data="adsb_fast_15"),
                 p.button("30", key="brand", callback_data="adsb_fast_30")],
                [p.button("NORMAL 15", key="brand", callback_data="adsb_normal_15"),
                 p.button("30", key="brand", callback_data="adsb_normal_30"),
                 p.button("60", key="brand", callback_data="adsb_normal_60")],
                [p.button("⬅️ REKLAMŲ VALDYMAS", key="brand", callback_data="adsb_home")],
            ]),
        )
        return True
    if data == "adsb_top3" or data.startswith("adsb_top3_"):
        if data == "adsb_top3_toggle":
            enabled = setting("ads_top3_enabled", "0") == "1"
            rb.set_setting("ads_top3_enabled", "0" if enabled else "1")
            rb.set_setting("ads_top3_force_send", "0")
            if not enabled:
                # Enabling is not authorization for an immediate public post.
                rb.set_setting("ads_top3_last_sent_ts", str(time.time()))
                rb.set_setting("ads_top3_last_result", "waiting")
        elif data == "adsb_top3_now":
            rb.set_setting("ads_top3_force_send", "1")
            rb.set_setting("ads_top3_last_result", "queued")
        elif data.startswith("adsb_top3_interval_"):
            minutes = int(data.removeprefix("adsb_top3_interval_"))
            if minutes in {60, 180, 360, 720, 1440}:
                rb.set_setting("ads_top3_interval_minutes", str(minutes))
        elif data == "adsb_top3_preview":
            admins = await context.bot.get_chat_administrators(rb.GROUP_CHAT)
            excluded = {member.user.id for member in admins} | rb.ADMIN_IDS
            rows = top3_ads.top3_rows(rb.DB_PATH, excluded)
            text_value = top3_ads.render_top3(rows) or "🏆 Savaitės TOP 3 dar tuščias — grupėje nieko neskelbsiu."
            await q.message.reply_text(
                text_value, parse_mode="HTML",
                reply_markup=top3_ads.invite_markup(os.getenv("BOT_USERNAME", "")) if rows else None,
            )
        enabled = setting("ads_top3_enabled", "0") == "1"
        minutes = setting("ads_top3_interval_minutes", "360")
        result = setting("ads_top3_last_result", "never")
        action = "IŠJUNGTI" if enabled else "ĮJUNGTI"
        await q.edit_message_text(
            f"🏆 <b>GYVAS SAVAITĖS TOP 3</b>\n\n"
            f"AUTO: {'ON' if enabled else 'OFF'} · kas {rb.esc(minutes)} min.\n"
            f"Paskutinis rezultatas: {rb.esc(result)}\n\n"
            "TOP skaičiuojamas iš naujo prieš kiekvieną siuntimą; grupės adminai nerodomi. "
            "Jei TOP tuščias, žinutė nesiunčiama. Įjungus pirmas automatinis įrašas "
            "bus tik po pasirinkto intervalo.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [p.button(f"AUTO {action}", key="trophy", callback_data="adsb_top3_toggle")],
                [p.button("👀 PERŽIŪRA", key="brand", callback_data="adsb_top3_preview"),
                 p.button("▶️ SIŲSTI DABAR", key="share", callback_data="adsb_top3_now")],
                [p.button("1 VAL.", key="brand", callback_data="adsb_top3_interval_60"),
                 p.button("3 VAL.", key="brand", callback_data="adsb_top3_interval_180"),
                 p.button("6 VAL.", key="brand", callback_data="adsb_top3_interval_360")],
                [p.button("12 VAL.", key="brand", callback_data="adsb_top3_interval_720"),
                 p.button("24 VAL.", key="brand", callback_data="adsb_top3_interval_1440")],
                [p.button("ATGAL", key="brand", callback_data="adsb_list_0")],
            ]),
        )
        return True
    if data.startswith("adsb_list_"):
        await ads_builder_list(q, int(data.removeprefix("adsb_list_") or "0"))
        return True
    if data in {"adsb_auto_start", "adsb_auto_stop"}:
        if data == "adsb_auto_stop":
            ads_builder_stop_rotation()
            await ads_builder_dashboard(q, "Reklamų AUTO sustabdytas.")
            return True
        if not ads_builder_start_rotation():
            await ads_builder_dashboard(q, "Pirmiausia „Visos reklamos“ pažymėk bent vieną patvirtintą ADS.")
            return True
        await ads_builder_dashboard(q, "Reklamų AUTO įjungtas.")
        return True
    if data.startswith("adsb_sendask_") or data.startswith("adsb_sendconfirm_"):
        confirm = data.startswith("adsb_sendconfirm_")
        prefix = "adsb_sendconfirm_" if confirm else "adsb_sendask_"
        identifier = data.removeprefix(prefix)
        row = ads_builder.active_by_id(rb.DB_PATH, int(identifier)) if identifier.isdigit() else None
        if not row:
            await ads_builder_dashboard(q, "Reklama nebėra patvirtinta; siuntimas atšauktas.")
            return True
        if confirm:
            if setting("ads_force_job_id", "0") not in {"", "0"}:
                await ads_builder_dashboard(q, "Kita reklama jau laukia siuntimo. Palauk jos rezultato.")
                return True
            rb.set_setting("ads_force_job_id", str(row["id"]))
            await ads_builder_dashboard(q, f"ADS „{row['note']}“ įtrauktas vienkartiniam siuntimui.")
        else:
            await q.edit_message_text(
                f"📣 Siųsti <b>{rb.esc(row['note'])}</b> į grupę vieną kartą dabar?\n"
                "Tai nepakeis automatinės rotacijos pasirinkimų.",
                parse_mode="HTML", reply_markup=InlineKeyboardMarkup([
                    [p.button("TAIP, SIŲSTI", key="share", callback_data=f"adsb_sendconfirm_{row['id']}")],
                    [p.button("ATŠAUKTI", key="brand", callback_data=f"adsb_view_{row['id']}")],
                ]),
            )
        return True
    if data.startswith("adsb_view_"):
        row = ads_builder.active_by_id(rb.DB_PATH, int(data.removeprefix("adsb_view_")))
        if not row:
            await ads_builder_list(q)
            return True
        chosen = row["note"] in ads_builder_selected()
        buttons = json.loads(row["buttons_json"])
        media_warning = (
            "\n⚠️ Sena media reklama: jei privatus šaltinis ištrintas, įkelk media iš naujo."
            if row["kind"] in {"photo", "video"} and not row.get("media_file_id") else ""
        )
        button_lines = "\n".join(
            f"{index}. {rb.esc(button['label'])} → {rb.esc(button['url'][:95])}"
            for index, button in enumerate(buttons, 1)
        ) or "—"
        await q.edit_message_text(
            f"📣 <b>{rb.esc(row['note'])}</b>\n"
            f"Bote: ✅ išsaugota · tipas: {row['kind']}\n"
            f"AUTO: {'pažymėtas' if chosen else 'nepažymėtas'}\n\n"
            f"<b>Tekstas:</b>\n{rb.esc(row['body'][:600])}\n\n"
            f"<b>Mygtukai:</b>\n{button_lines}{media_warning}",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [p.button("👀 PERŽIŪRA", key="brand", callback_data=f"adsb_preview_{row['id']}")],
                [p.button("📣 SIŲSTI DABAR", key="share", callback_data=f"adsb_sendask_{row['id']}")],
                [p.button("✏️ KEISTI TEKSTĄ / MEDIA", key="add", callback_data=f"adsb_edit_{row['id']}")],
                [p.button("🔗 REDAGUOTI MYGTUKUS", key="share", callback_data=f"adsb_editbuttons_{row['id']}")],
                [p.button("➖ IŠ AUTO" if chosen else "➕ Į AUTO", key="share", callback_data=f"adsb_toggle_{row['id']}")],
                [p.button("🗑 IŠTRINTI", key="brand", callback_data=f"adsb_deleteask_{row['id']}")],
                [p.button("ATGAL", key="brand", callback_data="adsb_list_0")],
            ]),
        )
        return True
    if data.startswith("adsb_legacy_") or data.startswith("adsb_replacelegacy_") or data.startswith("adsb_deletelegacy_"):
        managed = {row["note"] for row in ads_builder.active_ads(rb.DB_PATH)}
        legacy = [name for name in ads_builder_known_names() if name not in managed]
        index = int(data.rsplit("_", 1)[1])
        if index >= len(legacy):
            await ads_builder_list(q)
            return True
        note = legacy[index]
        if data.startswith("adsb_legacy_"):
            await q.edit_message_text(
                f"❔ <b>{rb.esc(note)}</b>\n\nSenas pavadinimas be išsaugoto turinio. "
                "Kad galėtų siųsti pats botas, įkelk reklamą iš naujo arba pašalink šį įrašą.",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [p.button("✏️ PERKURTI", key="add", callback_data=f"adsb_replacelegacy_{index}")],
                    [p.button("🗑 IŠTRINTI", key="brand", callback_data=f"adsb_deletelegacy_{index}")],
                    [p.button("ATGAL", key="brand", callback_data="adsb_list_0")],
                ]),
            )
        elif data.startswith("adsb_replacelegacy_"):
            context.user_data["ads_builder"] = {"step": "content", "note": note, "buttons": []}
            await q.edit_message_text(
                f"Atsiųsk naują {note} tekstą arba nuotrauką / video su caption.",
                reply_markup=ads_builder_back_menu(),
            )
        else:
            context.user_data["ads_builder"] = {"step": "delete", "note": note}
            await q.edit_message_text(
                f"Tikrai pašalinti seną įrašą {note} iš boto sąrašo?",
                reply_markup=InlineKeyboardMarkup([
                    [p.button("TAIP, IŠTRINTI", key="brand", callback_data="adsb_deleteconfirm")],
                    [p.button("ATŠAUKTI", key="brand", callback_data="adsb_cancel")],
                ]),
            )
        return True
    for prefix in ("adsb_preview_", "adsb_edit_", "adsb_editbuttons_", "adsb_toggle_", "adsb_deleteask_"):
        if not data.startswith(prefix):
            continue
        row = ads_builder.active_by_id(rb.DB_PATH, int(data.removeprefix(prefix)))
        if not row:
            await ads_builder_list(q)
            return True
        if prefix == "adsb_preview_":
            await ads_builder_preview(update, context, ads_builder_draft_from_row(row))
        elif prefix == "adsb_edit_":
            context.user_data["ads_builder"] = ads_builder_draft_from_row(row, step="content")
            await q.edit_message_text(
                f"Atsiųsk naują {row['note']} tekstą arba media su caption. Mygtukai išliks.",
                reply_markup=ads_builder_back_menu(),
            )
        elif prefix == "adsb_editbuttons_":
            context.user_data["ads_builder"] = ads_builder_draft_from_row(row)
            await ads_builder_buttons_screen(q, context.user_data["ads_builder"])
        elif prefix == "adsb_toggle_":
            selected = ads_builder_selected()
            if row["note"] in selected:
                selected.remove(row["note"])
                state = "pašalintas iš"
            else:
                selected.append(row["note"])
                state = "įtrauktas į"
            ads_builder_save_selected(selected)
            rb.set_setting("ads_enabled", "0")
            await q.edit_message_text(
                f"✅ {row['note']} {state} AUTO sąrašo. Rotacija sustabdyta; spausk AUTO START, kai baigsi.",
                reply_markup=InlineKeyboardMarkup([
                    [p.button("ESAMI ADS", key="brand", callback_data="adsb_list_0")],
                ]),
            )
        else:
            context.user_data["ads_builder"] = {"step": "delete", "note": row["note"]}
            await q.edit_message_text(
                f"Tikrai ištrinti reklamą {row['note']} iš boto? Jei ji pasirinkta AUTO, rotacija bus sustabdyta.",
                reply_markup=InlineKeyboardMarkup([
                    [p.button("TAIP, IŠTRINTI", key="brand", callback_data="adsb_deleteconfirm")],
                    [p.button("ATŠAUKTI", key="brand", callback_data="adsb_cancel")],
                ]),
            )
        return True
    if data == "adsb_deleteconfirm":
        draft = context.user_data.get("ads_builder")
        if not draft or draft.get("step") != "delete":
            await ads_builder_list(q)
            return True
        context.user_data.pop("ads_builder", None)
        name = draft["note"]
        was_rotating = setting("ads_enabled", "0") == "1" and name in ads_builder_running_notes()
        if was_rotating:
            ads_builder_stop_rotation()
        job_id = ads_builder.delete_direct(rb.DB_PATH, name, q.from_user.id)
        ads_builder_save_selected([item for item in ads_builder_selected() if item != name])
        for key in ("ads_notes", "ads_builder_known_notes"):
            try:
                names = json.loads(setting(key, "[]"))
                if isinstance(names, list):
                    rb.set_setting(key, json.dumps([item for item in names if item != name], ensure_ascii=False))
            except (TypeError, ValueError):
                pass
        await ads_builder_dashboard(q, f"Reklama „{name}“ ištrinta iš boto (#{job_id}).")
        return True
    if data == "adsb_buttons":
        draft = context.user_data.get("ads_builder")
        if draft and draft.get("step") == "confirm":
            await ads_builder_buttons_screen(q, draft)
        return True
    if data == "adsb_backconfirm":
        draft = context.user_data.get("ads_builder")
        if draft and draft.get("step") == "confirm":
            await q.edit_message_text(
                f"📣 {draft['note']} · pasirink veiksmą:",
                reply_markup=ads_builder_confirm_menu(len(draft.get("buttons", []))),
            )
        else:
            await ads_builder_dashboard(q)
        return True
    if data == "adsb_ba":
        draft = context.user_data.get("ads_builder")
        if not draft or draft.get("step") != "confirm":
            return True
        if len(draft.get("buttons", [])) >= 6:
            await q.edit_message_text("Daugiausia 6 URL mygtukai.")
            return True
        draft["step"] = "button_label"
        draft["button_index"] = None
        await q.edit_message_text(
            "Atsiųsk URL mygtuko pavadinimą (iki 40 simbolių). Gali įdėti vieną custom emoji — jis bus mygtuko ikona.",
            reply_markup=ads_builder_back_menu(),
        )
        return True
    if data.startswith("adsb_be_") or data.startswith("adsb_bd_"):
        draft = context.user_data.get("ads_builder")
        if not draft or draft.get("step") != "confirm":
            return True
        index = int(data.rsplit("_", 1)[1])
        if not 0 <= index < len(draft.get("buttons", [])):
            return True
        if data.startswith("adsb_bd_"):
            draft["buttons"].pop(index)
            await ads_builder_buttons_screen(q, draft)
        else:
            draft["step"] = "button_label"
            draft["button_index"] = index
            await q.edit_message_text(
                "Atsiųsk naują pavadinimą (iki 40 simbolių). Jei nori custom emoji ikonos, įdėk ją į šią žinutę.",
                reply_markup=ads_builder_back_menu(),
            )
        return True
    if data == "adsb_bdone":
        draft = context.user_data.get("ads_builder")
        if draft and draft.get("step") == "confirm":
            try:
                await ads_builder_preview(update, context, draft)
            except ValueError as exc:
                await q.edit_message_text(f"❌ {exc}", reply_markup=ads_builder_confirm_menu(len(draft.get("buttons", []))))
        return True
    return False


async def ads_builder_start(update, context):
    if update.effective_chat.type != ChatType.PRIVATE:
        await update.effective_message.reply_text("Atidaryk privatų @ERRORAS1_BOT pokalbį ir ten parašyk /adscreate.")
        return
    if not await rb.require_admin(update, context):
        return
    context.user_data.pop("prada_ui_edit", None)
    context.user_data.pop("ads_builder", None)
    await update.effective_message.reply_text(
        "📣 <b>NAUJA REKLAMA</b>\n\nĮrašyk savo pavadinimą, tada atsiųsk tekstą "
        "arba nuotrauką / video su caption. Prieš išsaugant parodysiu peržiūrą.",
        parse_mode="HTML", reply_markup=ads_builder_menu(),
    )


async def ads_builder_status(update, context):
    if update.effective_chat.type != ChatType.PRIVATE or not await rb.require_admin(update, context):
        return
    ads_builder.init(rb.DB_PATH)
    job = ads_builder.latest(rb.DB_PATH, update.effective_user.id)
    if not job:
        await update.effective_message.reply_text("ADS užduočių dar nėra. /adscreate")
        return
    await update.effective_message.reply_text(
        f"📣 ADS #{job['id']} · {job['note']}\n"
        f"Būsena: {job['status']}\n"
        f"{job['error'] or ''}"
    )


async def ads_panel_cmd(update, context):
    if not await rb.require_admin(update, context):
        return
    if update.effective_chat.type != ChatType.PRIVATE:
        await update.effective_message.reply_text("Reklamas valdyk privačiame @ERRORAS1_BOT pokalbyje: /ads")
        return
    text_value, markup = ads_builder_dashboard_payload()
    await update.effective_message.reply_text(text_value, parse_mode="HTML", reply_markup=markup)


async def ads_on_cmd(update, context):
    if not await rb.require_admin(update, context):
        return
    ads_builder.init(rb.DB_PATH)
    if not ads_builder_start_rotation():
        await update.effective_message.reply_text("Pirmiausia /ads meniu pasirink bent vieną patvirtintą reklamą.")
        return
    await update.effective_message.reply_text("✅ Reklamų AUTO įjungtas. Valdymas: /ads")


async def ads_off_cmd(update, context):
    if not await rb.require_admin(update, context):
        return
    ads_builder_stop_rotation()
    await update.effective_message.reply_text("⏸ Reklamų AUTO sustabdytas. Valdymas: /ads")


async def ads_set_cmd(update, context):
    if not await rb.require_admin(update, context):
        return
    ads_builder.init(rb.DB_PATH)
    requested = list(dict.fromkeys(arg.strip().lstrip("#") for arg in context.args))
    active = {row["note"] for row in ads_builder.active_ads(rb.DB_PATH)}
    if not requested or any(name not in active for name in requested):
        await update.effective_message.reply_text(
            "Naudok /adsset su patvirtintų reklamų pavadinimais iš /ads → Visos reklamos."
        )
        return
    ads_builder_stop_rotation()
    ads_builder_save_selected(requested)
    rb.set_setting("ads_notes", json.dumps(requested, ensure_ascii=False))
    await update.effective_message.reply_text("✅ Pasirinkta: " + ", ".join(requested) + ". AUTO liko išjungtas; /adson įjungia.")


async def ads_next_cmd(update, context):
    if not await rb.require_admin(update, context):
        return
    ads_builder.init(rb.DB_PATH)
    selected = ads_builder_active_selection()
    row = ads_builder.active_by_note(rb.DB_PATH, selected[0]) if selected else None
    if not row:
        await update.effective_message.reply_text("Nėra pasirinktos patvirtintos reklamos. Atidaryk /ads.")
        return
    if setting("ads_force_job_id", "0") not in {"", "0"}:
        await update.effective_message.reply_text("Viena reklama jau laukia siuntimo.")
        return
    rb.set_setting("ads_force_job_id", str(row["id"]))
    await update.effective_message.reply_text(f"⏳ {row['note']} suplanuota vienkartiniam siuntimui.")


async def ads_builder_button(update, context):
    q = update.callback_query
    await q.answer()
    if q.message.chat.type != ChatType.PRIVATE or not await rb.is_admin_user(q.from_user.id, context):
        return
    ads_builder.init(rb.DB_PATH)
    data = q.data or ""
    if await ads_builder_extended_button(update, context):
        return
    if data == "adsb_start":
        context.user_data.pop("prada_ui_edit", None)
        context.user_data.pop("ads_builder", None)
        await q.edit_message_text("📣 <b>NAUJA REKLAMA</b>\n\nSugalvok pavadinimą (pvz., konkursas arba akcija).", parse_mode="HTML", reply_markup=ads_builder_menu())
        return
    if data == "adsb_cancel":
        context.user_data.pop("ads_builder", None)
        await ads_builder_dashboard(q, "Redagavimas atšauktas.")
        return
    if data == "adsb_back":
        draft = context.user_data.get("ads_builder") or {}
        if draft.get("step") in {"button_label", "button_url"}:
            draft["step"] = "confirm"
            draft.pop("button_label", None)
            draft.pop("button_index", None)
            await ads_builder_buttons_screen(q, draft)
        elif draft.get("step") == "name":
            context.user_data.pop("ads_builder", None)
            await q.edit_message_text("📣 <b>NAUJA REKLAMA</b>\n\nSugalvok pavadinimą.", parse_mode="HTML", reply_markup=ads_builder_menu())
        else:
            context.user_data.pop("ads_builder", None)
            await ads_builder_dashboard(q)
        return
    if data == "adsb_name_custom":
        context.user_data["ads_builder"] = {"step": "name"}
        await q.edit_message_text(
            "Įrašyk reklamos pavadinimą: 1–64 raidės / skaičiai / _ / -.",
            reply_markup=ads_builder_back_menu(),
        )
        return
    if data.startswith("adsb_name_"):
        note = data.removeprefix("adsb_name_")
        if not ads_builder.NAME_RE.fullmatch(note):
            return
        existing = next((row for row in ads_builder.active_ads(rb.DB_PATH) if row["note"] == note), None)
        context.user_data["ads_builder"] = (
            ads_builder_draft_from_row(existing, step="content") if existing else
            {"step": "content", "note": note, "buttons": []}
        )
        await q.edit_message_text(
            f"📣 Užrašas: {note}\n\nAtsiųsk reklamos tekstą arba nuotrauką / video su caption.",
            reply_markup=ads_builder_back_menu(),
        )
        return
    if data not in {"adsb_save", "adsb_launch"}:
        return
    draft = context.user_data.get("ads_builder")
    if not draft or draft.get("step") != "confirm":
        await q.edit_message_text("Peržiūra nebegalioja. Pradėk iš naujo: /adscreate")
        return
    try:
        ads_builder.validate_direct_size(draft["kind"], draft["body"], draft.get("buttons", []))
    except ValueError as exc:
        await q.edit_message_text(f"❌ {exc}", reply_markup=ads_builder_confirm_menu(len(draft.get("buttons", []))))
        return
    ads_builder.init(rb.DB_PATH)
    if data == "adsb_launch" and setting("ads_force_job_id", "0") not in {"", "0"}:
        await q.edit_message_text("Kita reklama jau laukia siuntimo. Išsaugok be siuntimo arba palauk.", reply_markup=ads_builder_confirm_menu(len(draft.get("buttons", []))))
        return
    rotating = setting("ads_enabled", "0") == "1" and draft["note"] in ads_builder_running_notes()
    if rotating:
        ads_builder_stop_rotation()
    job_id = ads_builder.save_direct(
        rb.DB_PATH, draft["note"], q.from_user.id, draft["source_message_id"],
        draft["kind"], draft["body"],
        buttons=draft.get("buttons", []), source_chat_id=draft.get("source_chat_id"),
        media_file_id=draft.get("media_file_id", ""),
        entities=draft.get("entities", []),
    )
    if data == "adsb_launch":
        rb.set_setting("ads_force_job_id", str(job_id))
    context.user_data.pop("ads_builder", None)
    note = " Siuntimas į grupę suplanuotas." if data == "adsb_launch" else ""
    if rotating:
        note += " AUTO sustabdytas, nes keitei veikiančią reklamą."
    await ads_builder_dashboard(q, f"Reklama išsaugota bote (#{job_id}).{note}")


async def ads_builder_capture(update, context, kind, body):
    if update.effective_chat.type != ChatType.PRIVATE:
        return False
    draft = context.user_data.get("ads_builder")
    if not draft:
        return False
    if not await rb.is_admin_user(update.effective_user.id, context):
        return True
    msg = update.effective_message
    if draft.get("step") == "name":
        body = body.strip()
        if kind != "text" or not ads_builder.NAME_RE.fullmatch(body):
            await msg.reply_text("Pavadinimas netinka. Naudok 1–64 raides / skaičius / _ / -.", reply_markup=ads_builder_back_menu())
            return True
        existing = next((row for row in ads_builder.active_ads(rb.DB_PATH) if row["note"] == body), None)
        draft.update(ads_builder_draft_from_row(existing, step="content") if existing else
                     {"step": "content", "note": body, "buttons": []})
        await msg.reply_text("Dabar atsiųsk ADS tekstą arba nuotrauką / video su caption.", reply_markup=ads_builder_back_menu())
        return True
    if draft.get("step") == "button_label":
        if kind != "text":
            await msg.reply_text("Atsiųsk mygtuko pavadinimą kaip tekstą.", reply_markup=ads_builder_back_menu())
            return True
        try:
            label, icon_id = ads_button_label_with_icon(msg)
            ads_builder.validate_button(label, "https://example.com", icon_id)
        except ValueError as exc:
            await msg.reply_text(f"Netinkamas pavadinimas: {exc}. Naudok 1–40 simbolių ir daugiausia vieną custom emoji.", reply_markup=ads_builder_back_menu())
            return True
        draft["button_label"] = label
        draft["button_icon_id"] = icon_id
        draft["step"] = "button_url"
        await msg.reply_text("Dabar atsiųsk pilną URL, prasidedantį https:// arba http://.", reply_markup=ads_builder_back_menu())
        return True
    if draft.get("step") == "button_url":
        if kind != "text":
            await msg.reply_text("Atsiųsk URL kaip tekstą.", reply_markup=ads_builder_back_menu())
            return True
        try:
            button = ads_builder.validate_button(
                draft["button_label"], body.strip(), draft.get("button_icon_id", ""),
            )
            proposed = list(draft.get("buttons", []))
            index = draft.get("button_index")
            if index is None:
                proposed.append(button)
            else:
                proposed[index] = button
            ads_builder.validate_direct_size(draft["kind"], draft["body"], proposed)
        except (ValueError, IndexError) as exc:
            await msg.reply_text(f"❌ Netinkamas URL arba per ilga reklama: {exc}", reply_markup=ads_builder_back_menu())
            return True
        draft["buttons"] = proposed
        draft["step"] = "confirm"
        draft.pop("button_label", None)
        draft.pop("button_icon_id", None)
        draft.pop("button_index", None)
        await ads_builder_preview(update, context, draft)
        return True
    if draft.get("step") != "content":
        return True
    if kind in {"photo", "video"} and not body.strip():
        await msg.reply_text("Pridėk caption prie nuotraukos / video ir atsiųsk dar kartą.", reply_markup=ads_builder_back_menu())
        return True
    limit = 4096 if kind == "text" else 1024
    if not body.strip() or len(body) > limit:
        await msg.reply_text(f"ADS tekstas turi būti 1–{limit} simbolių.", reply_markup=ads_builder_back_menu())
        return True
    try:
        ads_builder.validate_direct_size(kind, body, draft.get("buttons", []))
    except ValueError as exc:
        await msg.reply_text(f"❌ {exc} Trumpink tekstą arba mygtukus.", reply_markup=ads_builder_back_menu())
        return True
    media_file_id = (
        msg.video.file_id if kind == "video" and msg.video else
        msg.photo[-1].file_id if kind == "photo" and msg.photo else ""
    )
    source_entities = getattr(msg, "entities" if kind == "text" else "caption_entities", None)
    entities = ads_builder.normalize_entities(
        [entity.to_dict() for entity in (source_entities or [])], body,
    )
    draft.update(
        step="confirm", kind=kind, body=body, source_message_id=msg.message_id,
        source_chat_id=update.effective_chat.id, media_file_id=media_file_id,
        entities=entities,
    )
    await ads_builder_preview(update, context, draft)
    return True


async def ads_builder_input(update, context):
    return await ads_builder_capture(update, context, "text", update.effective_message.text or "")


async def ads_builder_media(update, context):
    msg = update.effective_message
    if msg.video and msg.video.file_size and msg.video.file_size > 50 * 1024 * 1024:
        if context.user_data.get("ads_builder"):
            await msg.reply_text("Video per didelis. Maksimaliai 50 MB.")
        return
    await ads_builder_capture(update, context, "video" if msg.video else "photo", msg.caption or "")


async def ui_text_input(update, context):
    if await ads_builder_input(update, context):
        return
    pending = context.user_data.get("prada_ui_edit")
    if not pending:
        return
    if update.effective_chat.type != ChatType.PRIVATE:
        return
    if not await rb.is_admin_user(update.effective_user.id, context):
        return
    text = (update.effective_message.text or "").strip()
    if not text:
        return

    if pending.startswith("add_label:") and p.JACKIE_THEME:
        place = pending.split(":", 1)[1]
        if place not in PLACES:
            context.user_data.pop("prada_ui_edit", None)
            return
        try:
            label = validate_label(text)
        except ValueError as exc:
            await update.effective_message.reply_text(str(exc))
            return
        context.user_data["prada_ui_draft"] = {"place": place, "label": label}
        context.user_data["prada_ui_edit"] = f"add_url:{place}"
        await update.effective_message.reply_text(
            "Dabar atsiųsk pilną HTTPS nuorodą (pvz., https://t.me/tavo_kanalas)."
        )
        return

    if pending.startswith("add_url:") and p.JACKIE_THEME:
        place = pending.split(":", 1)[1]
        draft = context.user_data.get("prada_ui_draft")
        if not isinstance(draft, dict) or draft.get("place") != place:
            context.user_data.pop("prada_ui_edit", None)
            await update.effective_message.reply_text("Juodraštis neberastas. Pradėk pridėjimą iš naujo.")
            return
        try:
            url = validate_url(text)
        except ValueError as exc:
            await update.effective_message.reply_text(str(exc))
            return
        draft["url"] = url
        context.user_data["prada_ui_edit"] = "add_confirm"
        await update.effective_message.reply_text(
            f"<b>Naujo mygtuko peržiūra</b>\n\n"
            f"Vieta: {rb.esc(PLACES[place])}\n"
            f"Tekstas: {rb.esc(draft['label'])}\n"
            f"Nuoroda: {rb.esc(url)}\n\n"
            "Patvirtinus jis bus išsaugotas DB.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(draft["label"], url=url)],
                [p.button("✅ PATVIRTINTI", key="add", callback_data="ui_add_confirm")],
                [p.button("ATŠAUKTI", key="brand", callback_data="admin_ui")],
            ]),
        )
        return

    if pending == "add_confirm" and p.JACKIE_THEME:
        await update.effective_message.reply_text("Patvirtink mygtuką paspausdamas PERŽIŪROS mygtuką arba atšauk.")
        return

    if pending == "home":
        html_value = update.effective_message.text_html or rb.esc(text)
        rb.set_setting("prada_ui_home_text", text[:4000])
        rb.set_setting("prada_ui_home_html", html_value[:8000])
        context.user_data.pop("prada_ui_edit", None)
        await update.effective_message.reply_text("✅ /start tekstas + custom emoji išsaugoti DB.\n\nNaujas preview:")
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=dynamic_home_text(),
            parse_mode="HTML",
            reply_markup=dynamic_user_menu(True),
        )
        return

    if pending == "channel_caption" and p.JACKIE_THEME:
        if len(text) > 700:
            await update.effective_message.reply_text("❌ Per ilgas tekstas. Daugiausia 700 simbolių.")
            return
        rb.set_setting("jackie_info_body", text)
        context.user_data.pop("prada_ui_edit", None)
        result = await channel_info_refresh_result(context.bot)
        await update.effective_message.reply_text(
            "✅ INFO kanalo tekstas išsaugotas.\n" + result,
            reply_markup=ui_editor_menu(),
        )
        return

    if pending.startswith("icon:"):
        key = pending.split(":", 1)[1]
        if key not in BUTTON_DEFAULTS:
            context.user_data.pop("prada_ui_edit", None)
            return
        entities = list(update.effective_message.entities or [])
        custom = next(
            (
                getattr(entity, "custom_emoji_id", None)
                for entity in entities
                if getattr(entity, "type", None) == "custom_emoji"
                and getattr(entity, "custom_emoji_id", None)
            ),
            None,
        )
        if not custom:
            await update.effective_message.reply_text(
                "❌ Neradau Telegram CUSTOM EMOJI.\n"
                "Atsiųsk vieną Premium custom emoji iš Telegram emoji panelės (ne paprastą Unicode emoji)."
            )
            return
        rb.set_setting(f"prada_ui_icon_{key}", str(custom))
        context.user_data.pop("prada_ui_edit", None)
        await update.effective_message.reply_text(
            f"✅ Custom emoji išsaugotas mygtukui: {button_label(key)}",
            reply_markup=ui_buttons_menu(),
        )
        try:
            await rb.refresh_live_leaderboard(context.application)
        except Exception:
            pass
        return

    if pending.startswith("button:"):
        key = pending.split(":", 1)[1]
        if key not in BUTTON_DEFAULTS:
            context.user_data.pop("prada_ui_edit", None)
            return
        if len(text) > 64:
            await update.effective_message.reply_text("❌ Per ilgas. Atsiųsk iki 64 simbolių.")
            return
        rb.set_setting(f"prada_ui_button_{key}", text)
        context.user_data.pop("prada_ui_edit", None)
        channel_result = (
            "\n" + await channel_info_refresh_result(context.bot)
            if key.startswith("channel_") and p.JACKIE_THEME else ""
        )
        await update.effective_message.reply_text(
            f"✅ Mygtukas išsaugotas DB: {text}{channel_result}",
            reply_markup=ui_buttons_menu(),
        )
        try:
            await rb.refresh_live_leaderboard(context.application)
        except Exception:
            pass


async def on_button(update, context):
    q = update.callback_query
    data = q.data or ""

    if data.startswith("adsb_"):
        try:
            return await ads_builder_button(update, context)
        except BadRequest as exc:
            if "Message is not modified" in str(exc):
                return
            raise

    if data.startswith("admin_ads_"):
        await q.answer()
        if not await rb.is_admin_user(q.from_user.id, context):
            return
        if q.message.chat.type != ChatType.PRIVATE:
            await q.message.reply_text("Reklamas valdyk privačiame @ERRORAS1_BOT pokalbyje: /ads")
            return
        await ads_builder_dashboard(q, "Seni ADS mygtukai pakeisti vienu saugiu valdymo meniu.")
        return

    if data != "admin_ui" and not data.startswith("ui_"):
        return await bb.on_button(update, context)

    await q.answer()
    if not await rb.is_admin_user(q.from_user.id, context):
        return

    if data == "admin_ui":
        context.user_data.pop("prada_ui_edit", None)
        context.user_data.pop("prada_ui_draft", None)
        context.user_data.pop("ads_builder", None)
        await show_ui_editor(update, context, edit=True)
        return

    if data == "ui_add_confirm" and p.JACKIE_THEME:
        draft = context.user_data.get("prada_ui_draft")
        if not isinstance(draft, dict) or context.user_data.get("prada_ui_edit") != "add_confirm":
            await q.edit_message_text("Juodraštis neberastas. Pradėk iš naujo.", reply_markup=ui_editor_menu())
            return
        try:
            change_custom_buttons(lambda items: add_button(
                items, draft.get("place"), draft.get("label"), draft.get("url")
            ))
        except ValueError as exc:
            await q.edit_message_text(str(exc), reply_markup=ui_add_menu())
            return
        place = draft["place"]
        context.user_data.pop("prada_ui_edit", None)
        context.user_data.pop("prada_ui_draft", None)
        result = await refresh_custom_surface(place, context)
        await q.edit_message_text(
            "✅ Papildomas mygtukas išsaugotas." + result,
            reply_markup=ui_extras_menu(),
        )
        return

    if data == "ui_add" and p.JACKIE_THEME:
        context.user_data.pop("prada_ui_edit", None)
        context.user_data.pop("prada_ui_draft", None)
        await q.edit_message_text(
            "<b>PRIDĖTI MYGTUKĄ</b>\n\nPasirink, kur rodyti naują URL mygtuką:",
            parse_mode="HTML", reply_markup=ui_add_menu(),
        )
        return

    if data.startswith("ui_add_") and p.JACKIE_THEME:
        place = data[len("ui_add_"):]
        if place not in PLACES:
            return
        if len(buttons_for(custom_buttons(), place)) >= MAX_PER_PLACE:
            await q.edit_message_text(
                "Šioje vietoje jau yra daugiausia papildomų mygtukų.",
                reply_markup=ui_extras_menu(),
            )
            return
        context.user_data["prada_ui_edit"] = f"add_label:{place}"
        context.user_data.pop("prada_ui_draft", None)
        await q.edit_message_text(
            f"<b>{rb.esc(PLACES[place])}</b>\n\n"
            "Atsiųsk naujo mygtuko tekstą viena žinute (iki 64 simbolių).",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [p.button("ATŠAUKTI", key="brand", callback_data="admin_ui")]
            ]),
        )
        return

    if data == "ui_extras" and p.JACKIE_THEME:
        context.user_data.pop("prada_ui_edit", None)
        context.user_data.pop("prada_ui_draft", None)
        count = len(custom_buttons())
        await q.edit_message_text(
            f"<b>PAPILDOMI MYGTUKAI</b>\n\nIšsaugota: {count}. "
            "Pasirink mygtuką, jei nori jį pašalinti.",
            parse_mode="HTML", reply_markup=ui_extras_menu(),
        )
        return

    if data.startswith("ui_delete_confirm_") and p.JACKIE_THEME:
        identifier = data[len("ui_delete_confirm_"):]
        try:
            old = next(item for item in custom_buttons() if item["id"] == identifier)
            change_custom_buttons(lambda items: remove_button(items, identifier))
        except (StopIteration, ValueError):
            await q.edit_message_text("Mygtukas neberastas. Atnaujink sąrašą.", reply_markup=ui_extras_menu())
            return
        result = await refresh_custom_surface(old["place"], context)
        await q.edit_message_text("✅ Mygtukas pašalintas." + result, reply_markup=ui_extras_menu())
        return

    if data.startswith("ui_delete_") and p.JACKIE_THEME:
        identifier = data[len("ui_delete_"):]
        item = next((item for item in custom_buttons() if item["id"] == identifier), None)
        if item is None:
            return
        await q.edit_message_text(
            f"Pašalinti <b>{rb.esc(item['label'])}</b> iš {rb.esc(PLACES[item['place']])}?",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [p.button("TAIP, PAŠALINTI", key="crown", callback_data=f"ui_delete_confirm_{identifier}")],
                [p.button("ATGAL", key="brand", callback_data="ui_extras")],
            ]),
        )
        return

    if data.startswith("ui_extra_") and p.JACKIE_THEME:
        identifier = data[len("ui_extra_"):]
        item = next((item for item in custom_buttons() if item["id"] == identifier), None)
        if item is None:
            return
        await q.edit_message_text(
            f"<b>{rb.esc(item['label'])}</b>\n\n"
            f"Vieta: {rb.esc(PLACES[item['place']])}\n"
            f"Nuoroda: {rb.esc(item['url'])}",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [p.button("PAŠALINTI", key="crown", callback_data=f"ui_delete_{identifier}")],
                [p.button("ATGAL", key="brand", callback_data="ui_extras")],
            ]),
        )
        return

    if data == "ui_home":
        context.user_data["prada_ui_edit"] = "home"
        current = str(setting("prada_ui_home_text", "") or "")
        current_line = rb.esc(current) if current else "<i>naudojamas default tekstas</i>"
        await q.edit_message_text(
            "<b>REDAGUOTI /start TEKSTĄ</b>\n\n"
            "Atsiųsk naują tekstą viena paprasta žinute šiame private bote.\n"
            "Emoji gali naudoti. Pakeitimas iškart bus įrašytas į DB.\n\n"
            f"Dabar:\n{current_line}",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                [[p.button("ATŠAUKTI", key="brand", callback_data="admin_ui")]]
            ),
        )
        return

    if data == "ui_channel_caption" and p.JACKIE_THEME:
        context.user_data["prada_ui_edit"] = "channel_caption"
        current = str(setting("jackie_info_body", "") or "").strip()
        if not current:
            current = "✨ Naujienos · 🏆 Konkursai · 🔥 Savaitės TOP\n\n🔗 Prisijunk prie bendruomenės ir susikurk savo kvietimą ↓"
        await q.edit_message_text(
            "<b>INFO KANALO TEKSTAS</b>\n\n"
            "Atsiųsk naują pagrindinį tekstą viena žinute (iki 700 simbolių). "
            "Pavadinimas ir neoficialios bendruomenės pastaba liks. "
            "Prisegtas įrašas bus atnaujintas vietoje.\n\n"
            f"Dabar:\n{rb.esc(current)}",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                [[p.button("ATŠAUKTI", key="brand", callback_data="admin_ui")]]
            ),
        )
        return

    if data == "ui_buttons":
        context.user_data.pop("prada_ui_edit", None)
        await q.edit_message_text(
            "<b>REDAGUOTI MYGTUKUS</b>\n\nPasirink kurį mygtuką pervadinti:",
            parse_mode="HTML",
            reply_markup=ui_buttons_menu(),
        )
        return

    if data.startswith("ui_btntext_"):
        key = data[len("ui_btntext_"):]
        if key not in BUTTON_DEFAULTS:
            return
        context.user_data["prada_ui_edit"] = f"button:{key}"
        await q.edit_message_text(
            f"<b>{rb.esc(button_label(key))}</b>\n\n"
            "Atsiųsk naują šio mygtuko tekstą viena žinute (iki 64 simbolių).",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                [[p.button("ATŠAUKTI", key="brand", callback_data=f"ui_btn_{key}")]]
            ),
        )
        return

    if data.startswith("ui_btnemoji_"):
        key = data[len("ui_btnemoji_"):]
        if key not in BUTTON_DEFAULTS:
            return
        context.user_data["prada_ui_edit"] = f"icon:{key}"
        await q.edit_message_text(
            f"<b>{rb.esc(button_label(key))}</b>\n\n"
            "Dabar atsiųsk <b>vieną Telegram Premium CUSTOM EMOJI</b>.\n"
            "Botas nuskaitys jo custom emoji ID ir naudos kaip mygtuko ikoną.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                [[p.button("ATŠAUKTI", key="brand", callback_data=f"ui_btn_{key}")]]
            ),
        )
        return

    if data.startswith("ui_btnclearicon_"):
        key = data[len("ui_btnclearicon_"):]
        if key not in BUTTON_DEFAULTS:
            return
        rb.set_setting(f"prada_ui_icon_{key}", "")
        context.user_data.pop("prada_ui_edit", None)
        await q.edit_message_text(
            f"<b>{rb.esc(button_label(key))}</b>\n\nCustom emoji nuimtas. Bus naudojama default pack ikona.",
            parse_mode="HTML",
            reply_markup=ui_button_edit_menu(key),
        )
        try:
            await rb.refresh_live_leaderboard(context.application)
        except Exception:
            pass
        return

    if data.startswith("ui_btn_"):
        key = data[len("ui_btn_"):]
        if key not in BUTTON_DEFAULTS:
            return
        context.user_data.pop("prada_ui_edit", None)
        icon_state = "asmeninis" if str(setting(f"prada_ui_icon_{key}", "") or "").strip() else "default"
        channel_note = "\n\n<i>Teksto pakeitimas atnaujins prisegtą INFO įrašą.</i>" if key.startswith("channel_") and p.JACKIE_THEME else ""
        await q.edit_message_text(
            f"<b>{rb.esc(button_label(key))}</b>\n\n"
            f"Custom emoji: <b>{icon_state}</b>\n"
            "Pasirink ką keisti:"
            f"{channel_note}",
            parse_mode="HTML",
            reply_markup=ui_button_edit_menu(key),
        )
        return

    if data == "ui_reset":
        rb.set_setting("prada_ui_home_text", "")
        rb.set_setting("prada_ui_home_html", "")
        for key in BUTTON_KEYS:
            rb.set_setting(f"prada_ui_button_{key}", "")
            rb.set_setting(f"prada_ui_icon_{key}", "")
        if p.JACKIE_THEME:
            rb.set_setting("jackie_info_body", "")
            rb.set_setting(SETTING_KEY, "[]")
        context.user_data.pop("prada_ui_edit", None)
        channel_result = "\n" + await channel_info_refresh_result(context.bot) if p.JACKIE_THEME else ""
        await q.edit_message_text("✅ UI grąžintas į default." + channel_result, reply_markup=ui_editor_menu())
        try:
            await rb.refresh_live_leaderboard(context.application)
        except Exception:
            pass
        return

    if data == "ui_back_admin":
        context.user_data.pop("prada_ui_edit", None)
        await q.edit_message_text(
            f"{p.brand_line()} · <b>ADMIN PANEL</b>",
            parse_mode="HTML",
            reply_markup=dynamic_admin_menu(),
        )


async def set_command_menus(application):
    user_commands = [
        BotCommand("start", f"Atidaryti {p.BRAND_NAME} meniu"),
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
            BotCommand("uiedit", "Redaguoti /start ir mygtukus"),
            BotCommand("sethome", "Nustatyti /start tekstą"),
            BotCommand("setbtn", "Pervadinti mygtuką"),
            BotCommand("setbtnicon", "Custom emoji mygtukui"),
            BotCommand("resetui", "Atstatyti UI"),
            BotCommand("addpoints", "Pridėti taškų"),
            BotCommand("takepoints", "Atimti taškų"),
            BotCommand("setpoints", "Nustatyti taškus"),
            BotCommand("resetpoints", "Nunulinti taškus"),
            BotCommand("userinfo", "Vartotojo taškai / ID"),
            BotCommand("ads", "ADS status"),
            BotCommand("adscreate", "Sukurti ar redaguoti ADS"),
            BotCommand("adsjob", "Paskutinio ADS darbo būsena"),
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


p.home_text = dynamic_home_text
p.user_menu = dynamic_user_menu
p.admin_menu = dynamic_admin_menu
p.invite_markup = dynamic_invite_markup
p.live_markup = dynamic_live_markup
p.native_ad_markup = dynamic_live_markup
p.set_command_menus = set_command_menus

bb.home_text = dynamic_home_text
bb.user_menu = dynamic_user_menu
bb.admin_menu = dynamic_admin_menu
bb.set_command_menus = set_command_menus

rb.user_menu = dynamic_user_menu
rb.admin_menu = dynamic_admin_menu
rb.invite_markup = dynamic_invite_markup
rb.live_markup = dynamic_live_markup
rb.native_ad_markup = dynamic_live_markup
rb.set_command_menus = set_command_menus


async def branded_post_init(application):
    await rb.post_init(application)
    if not p.JACKIE_THEME:
        return
    profile_fields = (
        ("name", application.bot.set_my_name, "JACKIE CHAN COMMUNITY"),
        (
            "description",
            application.bot.set_my_description,
            "🥋 Jackie Chan Community — pakviesk draugus, rink taškus ir kilk į savaitės TOP. "
            "Neoficiali bendruomenė.",
        ),
        (
            "short_description",
            application.bot.set_my_short_description,
            "🐉 Kvietimai, taškai ir savaitės TOP viename dojo.",
        ),
    )
    for field, setter, value in profile_fields:
        try:
            await setter(**{field: value})
        except Exception:
            rb.log.exception("Nepavyko atnaujinti Jackie boto profilio lauko %s", field)
    if rb.get_setting("jackie_profile_photo_synced", "0") != "1":
        try:
            portrait = Path(__file__).resolve().parent / "assets" / "jackie_shop.jpg"
            # A Path becomes a local file URI in PTB; Telegram's cloud API
            # requires multipart bytes for setMyProfilePhoto.
            with portrait.open("rb") as portrait_file:
                await application.bot.set_my_profile_photo(InputProfilePhotoStatic(portrait_file))
            rb.set_setting("jackie_profile_photo_synced", "1")
        except Exception:
            rb.log.exception("Nepavyko atnaujinti Jackie boto profilio nuotraukos")


def main():
    bb.ensure_adjustment_table()
    rb.init_db()

    app = (
        Application.builder()
        .token(rb.BOT_TOKEN)
        .post_init(branded_post_init)
        .post_shutdown(rb.post_shutdown)
        .build()
    )

    handlers = [
        ("start", bb.start_cmd),
        ("mylink", rb.mylink_cmd),
        ("points", bb.points_cmd),
        ("top", rb.top_cmd),
        ("leaderboard", rb.top_cmd),
        ("top10", rb.top_cmd),
        ("alltime", rb.alltime_cmd),
        ("lastweek", rb.lastweek_cmd),
        ("how", bb.how_cmd),
        ("kaipveikia", bb.how_cmd),
        ("admin", p.admin_cmd),
        ("stats", bb.stats_cmd),
        ("addpoints", bb.addpoints_cmd),
        ("takepoints", bb.takepoints_cmd),
        ("removepoints", bb.takepoints_cmd),
        ("setpoints", bb.setpoints_cmd),
        ("resetpoints", bb.resetpoints_cmd),
        ("userinfo", bb.userinfo_cmd),
        ("pointhelp", bb.pointhelp_cmd),
        ("uiedit", uiedit_cmd),
        ("sethome", sethome_cmd),
        ("setbtn", setbtn_cmd),
        ("setbtnicon", setbtnicon_cmd),
        ("resetui", resetui_cmd),
        ("ads", ads_panel_cmd),
        ("adsset", ads_set_cmd),
        ("adsinterval", rb.adsinterval_cmd),
        ("adsfast", rb.adsfast_cmd),
        ("adson", ads_on_cmd),
        ("adsoff", ads_off_cmd),
        ("adsnext", ads_next_cmd),
        ("contestad", rb.contestad_cmd),
        ("promoad", rb.promoad_cmd),
        ("adpack", rb.adpack_cmd),
        ("liveboard", rb.liveboard_cmd),
        ("liveboardnew", rb.liveboardnew_cmd),
        ("liveboardoff", rb.liveboardoff_cmd),
        ("adminnote", bb.adminnote_cmd),
    ]
    for name, handler in handlers:
        app.add_handler(CommandHandler(name, handler))

    app.add_handler(CommandHandler("adscreate", ads_builder_start))
    app.add_handler(CommandHandler("adsjob", ads_builder_status))

    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(ChatMemberHandler(bb.on_chat_member, ChatMemberHandler.CHAT_MEMBER))
    app.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND,
            ui_text_input,
        )
    )
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & (filters.PHOTO | filters.VIDEO), ads_builder_media))

    mode = "custom" if _USE_BUTTON_CUSTOM else "unicode"
    print(f"{p.BRAND_NAME} UI editor: DB-backed text/buttons; button icons={mode}")
    print(f"Grupė: {rb.GROUP_CHAT_RAW}")
    print(f"Savaitės reset: pirmadienį 00:00 ({rb.WEEK_TIMEZONE})")

    app.run_polling(
        allowed_updates=["message", "callback_query", "chat_member", "my_chat_member"],
        drop_pending_updates=False,
    )


if __name__ == "__main__":
    main()
