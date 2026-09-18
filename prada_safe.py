"""Safe PRADA LUX runtime wrapper with DB-backed UI editor.

- Message text uses safe Unicode fallbacks.
- Inline buttons can keep custom emoji icons.
- Admin can edit the /start text and button labels from Telegram.
- UI changes are stored in the existing SQLite settings table and survive restarts.
"""

import os

import prada_ui as p
import branded_bot as bb
import referral_bot as rb
from telegram import BotCommand, BotCommandScopeChatAdministrators, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatType
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ChatMemberHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

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
    "channel_group": "GRUPĖ",
    "channel_invite": "MANO INVITE",
}
BUTTON_KEYS = tuple(BUTTON_DEFAULTS)


def safe_icon_id(key):
    if not _USE_BUTTON_CUSTOM:
        return None
    value = _PRADA_EMOJI_IDS.get(key)
    return str(value).strip() if value else None


p.icon_id = safe_icon_id
rb.EMOJI_IDS = {}


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


def dynamic_home_text():
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
    return InlineKeyboardMarkup(rows)


def dynamic_admin_menu():
    return InlineKeyboardMarkup([
        [
            p.button("ADS STATUS", key="share", callback_data="admin_ads_status"),
            p.button("ADS DABAR", key="share", callback_data="admin_ads_next"),
        ],
        [
            p.button("ADS ON", key="add", callback_data="admin_ads_on"),
            p.button("ADS OFF", key="crown", callback_data="admin_ads_off"),
        ],
        [
            p.button("REFRESH TOP", key="trophy", callback_data="admin_live_refresh"),
            p.button("STATISTIKA", key="stats", callback_data="admin_stats"),
        ],
        [p.button("TAŠKŲ VALDYMAS", key="add", callback_data="admin_points_help")],
        [p.button("UI REDAGUOTI", key="brand", callback_data="admin_ui")],
        [
            p.button("ADS PREVIEW", key="brand", callback_data="admin_adpack"),
            p.button("KOMANDOS", key="crown", callback_data="admin_commands"),
        ],
        [p.button("ATGAL", key="brand", callback_data="back")],
    ])


def dynamic_invite_markup(link):
    rows = [[dynamic_button("share", fallback_icon="share", url=p.share_url(link))]]
    if rb.GROUP_PUBLIC_URL:
        rows.append([dynamic_button("open_group", fallback_icon="brand", url=rb.GROUP_PUBLIC_URL)])
    return InlineKeyboardMarkup(rows)


def dynamic_live_markup(application):
    url = rb.bot_url(application, "invite")
    rows = []
    if url:
        rows.append([dynamic_button("live_invite", fallback_icon="invite", url=url)])
    if rb.GROUP_PUBLIC_URL:
        rows.append([dynamic_button("live_group", fallback_icon="brand", url=rb.GROUP_PUBLIC_URL)])
    return InlineKeyboardMarkup(rows) if rows else None


def ui_editor_menu():
    return InlineKeyboardMarkup([
        [p.button("REDAGUOTI TEKSTĄ", key="brand", callback_data="ui_home")],
        [p.button("REDAGUOTI MYGTUKUS", key="stats", callback_data="ui_buttons")],
        [p.button("ATSTATYTI DEFAULT", key="crown", callback_data="ui_reset")],
        [p.button("ATGAL Į ADMIN", key="brand", callback_data="ui_back_admin")],
    ])


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
    rows.append([p.button("ATGAL", key="brand", callback_data="admin_ui")])
    return InlineKeyboardMarkup(rows)


def ui_button_edit_menu(key):
    return InlineKeyboardMarkup([
        [p.button("KEISTI TEKSTĄ", key="brand", callback_data=f"ui_btntext_{key}")],
        [p.button("NUSTATYTI CUSTOM EMOJI", key="crown", callback_data=f"ui_btnemoji_{key}")],
        [p.button("NUIMTI CUSTOM EMOJI", key="brand", callback_data=f"ui_btnclearicon_{key}")],
        [p.button("ATGAL", key="brand", callback_data="ui_buttons")],
    ])


async def show_ui_editor(update, context, edit=False):
    text = (
        "<b>PRADA LUX · UI REDAKTORIUS</b>\n\n"
        "Pakeitimai saugomi SQLite DB ir lieka po restart.\n"
        "Gali keisti /start tekstą, mygtukų tekstą ir jų Premium custom emoji ikoną."
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
    if reply:
        value = reply.text or reply.caption
    elif context.args:
        value = " ".join(context.args).replace("\\n", "\n")
    if not value:
        context.user_data["prada_ui_edit"] = "home"
        await update.effective_message.reply_text(
            "Atsiųsk naują /start tekstą viena žinute. Jis bus išsaugotas DB."
        )
        return
    rb.set_setting("prada_ui_home_text", value[:4000])
    await update.effective_message.reply_text("✅ /start tekstas išsaugotas DB.")


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
    await update.effective_message.reply_text(f"✅ Mygtukas {key} → {label}")
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
    for key in BUTTON_KEYS:
        rb.set_setting(f"prada_ui_button_{key}", "")
        rb.set_setting(f"prada_ui_icon_{key}", "")
    context.user_data.pop("prada_ui_edit", None)
    await update.effective_message.reply_text("✅ UI grąžintas į default.")
    try:
        await rb.refresh_live_leaderboard(context.application)
    except Exception:
        pass


async def ui_text_input(update, context):
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

    if pending == "home":
        rb.set_setting("prada_ui_home_text", text[:4000])
        context.user_data.pop("prada_ui_edit", None)
        await update.effective_message.reply_text("✅ /start tekstas išsaugotas DB.\n\nNaujas preview:")
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=dynamic_home_text(),
            parse_mode="HTML",
            reply_markup=dynamic_user_menu(True),
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
        await update.effective_message.reply_text(
            f"✅ Mygtukas išsaugotas DB: {text}",
            reply_markup=ui_buttons_menu(),
        )
        try:
            await rb.refresh_live_leaderboard(context.application)
        except Exception:
            pass


async def on_button(update, context):
    q = update.callback_query
    data = q.data or ""

    if data != "admin_ui" and not data.startswith("ui_"):
        return await bb.on_button(update, context)

    await q.answer()
    if not await rb.is_admin_user(q.from_user.id, context):
        return

    if data == "admin_ui":
        context.user_data.pop("prada_ui_edit", None)
        await show_ui_editor(update, context, edit=True)
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
        await q.answer("Custom emoji nuimtas")
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
        await q.edit_message_text(
            f"<b>{rb.esc(button_label(key))}</b>\n\n"
            f"Custom emoji: <b>{icon_state}</b>\n"
            "Pasirink ką keisti:",
            parse_mode="HTML",
            reply_markup=ui_button_edit_menu(key),
        )
        return

    if data == "ui_reset":
        rb.set_setting("prada_ui_home_text", "")
        for key in BUTTON_KEYS:
            rb.set_setting(f"prada_ui_button_{key}", "")
            rb.set_setting(f"prada_ui_icon_{key}", "")
        context.user_data.pop("prada_ui_edit", None)
        await q.edit_message_text("✅ UI grąžintas į default.", reply_markup=ui_editor_menu())
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


def main():
    bb.ensure_adjustment_table()
    rb.init_db()

    app = (
        Application.builder()
        .token(rb.BOT_TOKEN)
        .post_init(rb.post_init)
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
        ("ads", rb.ads_cmd),
        ("adsset", rb.adsset_cmd),
        ("adsinterval", rb.adsinterval_cmd),
        ("adsfast", rb.adsfast_cmd),
        ("adson", rb.adson_cmd),
        ("adsoff", rb.adsoff_cmd),
        ("adsnext", rb.adsnext_cmd),
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

    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(ChatMemberHandler(bb.on_chat_member, ChatMemberHandler.CHAT_MEMBER))
    app.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND,
            ui_text_input,
        )
    )

    mode = "custom" if _USE_BUTTON_CUSTOM else "unicode"
    print(f"PRADA LUX UI editor: DB-backed text/buttons; button icons={mode}")
    print(f"Grupė: {rb.GROUP_CHAT_RAW}")
    print(f"Savaitės reset: pirmadienį 00:00 ({rb.WEEK_TIMEZONE})")

    app.run_polling(
        allowed_updates=["message", "callback_query", "chat_member", "my_chat_member"],
        drop_pending_updates=False,
    )


if __name__ == "__main__":
    main()
