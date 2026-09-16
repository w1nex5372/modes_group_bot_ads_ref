"""NERA DROPO live bot layer.

- Uses the NERA DROPO custom emoji pack in visible bot text.
- live_ui.py supplies custom inline icons with default button colours.
- Removes the message-count leaderboard from the live bot.
- Adds admin point management and audit logging.
"""

import asyncio
import json
from contextlib import closing

import referral_bot as rb
from telegram import BotCommand, BotCommandScopeChatAdministrators, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatType, ParseMode
from telegram.ext import Application, CallbackQueryHandler, ChatMemberHandler, CommandHandler


# ---------------------------------------------------------------------------
# Brand / visible text
# ---------------------------------------------------------------------------

def brand_line():
    return f"{rb.em('brand')} <b>NĖRA DROPO</b>"


def home_text():
    """Same welcome for /start and Back, independent of the connected test group."""
    return (
        f"{brand_line()}\n"
        "Kviesk draugus, rink taškus ir kilk į TOP.\n\n"
        f"{rb.em('invite')} Naujas narys per tavo nuorodą = <b>+1</b>\n"
        f"{rb.em('add')} Pridedi naują narį per <b>Add Members</b> = <b>+1</b>\n\n"
        f"{rb.em('trophy')} Abu būdai sumuojasi. Tas pats žmogus – 1 kartą.\n"
        f"{rb.em('stats')} Nauja savaitė: pirmadienį 00:00.\n\n"
        f"{rb.em('share')} Pradėk nuo <b>MANO INVITE</b>."
    )


def user_menu(is_admin=False):
    rows = [
        [
            InlineKeyboardButton("🔗 MANO INVITE", callback_data="my_link"),
            InlineKeyboardButton("⭐ MANO TAŠKAI", callback_data="points"),
        ],
        [
            InlineKeyboardButton("🏆 INVITE TOP", callback_data="top"),
            InlineKeyboardButton("📈 VISO TOP", callback_data="alltime"),
        ],
        [
            InlineKeyboardButton("🥇 PRAEITA SAVAITĖ", callback_data="lastweek"),
            InlineKeyboardButton("ℹ️ KAIP VEIKIA", callback_data="info"),
        ],
    ]
    if is_admin:
        rows.append([InlineKeyboardButton("🛠 ADMIN PANEL", callback_data="admin_panel")])
    return InlineKeyboardMarkup(rows)


def admin_menu():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📣 ADS STATUS", callback_data="admin_ads_status"),
            InlineKeyboardButton("⏭ ADS DABAR", callback_data="admin_ads_next"),
        ],
        [
            InlineKeyboardButton("🟢 ADS ON", callback_data="admin_ads_on"),
            InlineKeyboardButton("🔴 ADS OFF", callback_data="admin_ads_off"),
        ],
        [
            InlineKeyboardButton("🏆 REFRESH TOP", callback_data="admin_live_refresh"),
            InlineKeyboardButton("📊 STATISTIKA", callback_data="admin_stats"),
        ],
        [
            InlineKeyboardButton("🎛 TAŠKŲ VALDYMAS", callback_data="admin_points_help"),
        ],
        [
            InlineKeyboardButton("🖼 ADS PREVIEW", callback_data="admin_adpack"),
            InlineKeyboardButton("📋 KOMANDOS", callback_data="admin_commands"),
        ],
        [InlineKeyboardButton("⬅️ ATGAL", callback_data="back")],
    ])


def weekly_top_text(excluded=None):
    rows = rb.weekly_top(exclude_ids=excluded)
    lines = [f"{rb.em('trophy')} <b>SAVAITĖS INVITE TOP 10</b>", ""]
    lines += rb.ranking_lines(rows, "weekly_points", "tšk.") if rows else [
        f"{rb.em('brand')} Kol kas TOP tuščias"
    ]
    lines += [
        "",
        f"{rb.em('stats')} Reset: pirmadienį 00:00",
        brand_line(),
    ]
    return "\n".join(lines)


def alltime_top_text(excluded=None):
    rows = rb.alltime_top(exclude_ids=excluded)
    lines = [f"{rb.em('stats')} <b>VISO LAIKO INVITE TOP 10</b>", ""]
    lines += rb.ranking_lines(rows, "points", "tšk.") if rows else [
        f"{rb.em('brand')} Kol kas TOP tuščias"
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
    lines = [f"{rb.em('trophy')} <b>SAVAITĖS INVITE TOP 10 · LIVE</b>", ""]
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
        f"{rb.em('invite')} <b>TAVO INVITE</b>\n\n"
        f"{rb.esc(link)}\n\n"
        f"{rb.em('trophy')} <b>+1 taškas</b> už kiekvieną naują narį:\n"
        f"{rb.em('share')} žmogus ateina per tavo nuorodą\n"
        f"{rb.em('add')} arba pats jį pridedi per <b>Add Members</b>\n\n"
        f"{rb.em('brand')} Abu būdai sumuojasi. Tas pats žmogus skaičiuojamas 1 kartą.\n\n"
        f"{brand_line()}"
    )


def contest_rose_caption(application):
    b = rb.bot_url(application, "invite") or "https://t.me/TAVO_BOTO_USERNAME"
    g = rb.GROUP_PUBLIC_URL or "https://t.me/NERADAUDROPO"
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
        f"[🎯 DALYVAUTI](buttonurl://{b})",
        f"[👑 NĖRA DROPO](buttonurl://{g}:same)",
    ])


def promo_rose_caption(application):
    b = rb.bot_url(application, "invite") or "https://t.me/TAVO_BOTO_USERNAME"
    g = rb.GROUP_PUBLIC_URL or "https://t.me/NERADAUDROPO"
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
        f"[🤖 ATIDARYTI BOTĄ](buttonurl://{b})",
        f"[🚀 ATIDARYTI GRUPĘ](buttonurl://{g}:same)",
    ])


def ads_status_text():
    try:
        notes = json.loads(rb.get_setting("ads_notes", "[]"))
    except Exception:
        notes = []
    state = "ON" if rb.get_setting("ads_enabled", "0") == "1" else "OFF"
    return (
        f"{rb.em('share')} <b>AUTO ADS</b>\n\n"
        f"{rb.em('brand')} Būsena: <b>{state}</b>\n"
        f"{rb.em('stats')} FAST: kas {rb.esc(rb.get_setting('ads_fast_interval_minutes','15'))} min.\n"
        f"{rb.em('stats')} NORMAL: kas {rb.esc(rb.get_setting('ads_interval_minutes','30'))} min.\n"
        f"{rb.em('invite')} Notes: {rb.esc(', '.join(notes) if notes else '—')}\n"
        f"{rb.em('trophy')} Paskutinis: {rb.esc(rb.get_setting('ads_last_note','—') or '—')}\n"
        f"{rb.em('crown')} Rezultatas: {rb.esc(rb.get_setting('ads_last_result','—') or '—')}\n\n"
        f"{brand_line()}"
    )


def point_admin_help():
    return (
        f"{rb.em('crown')} <b>TAŠKŲ VALDYMAS</b>\n\n"
        f"{rb.em('add')} <code>/addpoints @user 5</code> — pridėti 5\n"
        f"{rb.em('add')} Reply į žmogaus žinutę: <code>/addpoints 5</code>\n"
        f"{rb.em('stats')} <code>/takepoints @user 2</code> — atimti 2\n"
        f"{rb.em('trophy')} <code>/setpoints @user 10 25</code> — savaitė 10, viso 25\n"
        f"{rb.em('brand')} <code>/resetpoints @user</code> — nunulinti\n"
        f"{rb.em('invite')} <code>/userinfo @user</code> — patikrinti\n\n"
        f"Galima naudoti Telegram ID vietoje @username.\n"
        f"Po pakeitimo LIVE TOP atsinaujina automatiškai."
    )


# ---------------------------------------------------------------------------
# Admin point management
# ---------------------------------------------------------------------------

def ensure_adjustment_table():
    with closing(rb.db()) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS point_adjustments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_id INTEGER NOT NULL,
                target_user_id INTEGER NOT NULL,
                weekly_before INTEGER NOT NULL,
                weekly_after INTEGER NOT NULL,
                total_before INTEGER NOT NULL,
                total_after INTEGER NOT NULL,
                action TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


def find_user_row(target):
    if target is None:
        return None
    raw = str(target).strip()
    with closing(rb.db()) as conn:
        if raw.lstrip("-").isdigit():
            return conn.execute("SELECT * FROM users WHERE user_id=?", (int(raw),)).fetchone()
        username = raw.lstrip("@").lower()
        return conn.execute("SELECT * FROM users WHERE LOWER(username)=?", (username,)).fetchone()


def target_from_update(update, args, amount_count=1):
    reply = getattr(update.effective_message, "reply_to_message", None)
    if reply and reply.from_user:
        return str(reply.from_user.id), list(args)
    if not args:
        return None, []
    return args[0], list(args[1:])


def adjust_points(admin_id, row, new_weekly, new_total, action):
    ensure_adjustment_table()
    old_weekly = int(row["weekly_points"] or 0)
    old_total = int(row["points"] or 0)
    new_weekly = max(0, int(new_weekly))
    new_total = max(0, int(new_total))
    with closing(rb.db()) as conn:
        conn.execute(
            "UPDATE users SET weekly_points=?, points=? WHERE user_id=?",
            (new_weekly, new_total, int(row["user_id"])),
        )
        conn.execute(
            """
            INSERT INTO point_adjustments(
                admin_id,target_user_id,weekly_before,weekly_after,
                total_before,total_after,action,created_at
            ) VALUES(?,?,?,?,?,?,?,?)
            """,
            (
                int(admin_id), int(row["user_id"]), old_weekly, new_weekly,
                old_total, new_total, action, rb.now_iso(),
            ),
        )
        conn.commit()
    return old_weekly, old_total, new_weekly, new_total


async def point_result(update, context, row, values, action):
    old_week, old_total, new_week, new_total = values
    name = f"@{row['username']}" if row["username"] else f"ID {row['user_id']}"
    await update.effective_message.reply_text(
        f"{rb.em('trophy')} <b>TAŠKAI ATNAUJINTI</b>\n\n"
        f"{rb.em('group')} {rb.esc(name)}\n"
        f"{rb.em('crown')} Savaitė: {old_week} → <b>{new_week}</b>\n"
        f"{rb.em('stats')} Viso: {old_total} → <b>{new_total}</b>\n"
        f"{rb.em('brand')} Veiksmas: {rb.esc(action)}",
        parse_mode=ParseMode.HTML,
    )
    await rb.refresh_live_leaderboard(context.application)


async def addpoints_cmd(update, context):
    if not await rb.require_admin(update, context):
        return
    target, args = target_from_update(update, context.args)
    if target is None or not args:
        await update.effective_message.reply_text(point_admin_help(), parse_mode=ParseMode.HTML)
        return
    try:
        amount = int(args[0])
        if amount <= 0:
            raise ValueError
    except ValueError:
        await update.effective_message.reply_text("❌ Taškų kiekis turi būti teigiamas skaičius.")
        return
    row = find_user_row(target)
    if not row:
        await update.effective_message.reply_text("❌ Vartotojo DB neradau. Tegul jis bent kartą atidaro botą arba naudok Reply.")
        return
    values = adjust_points(
        update.effective_user.id,
        row,
        int(row["weekly_points"]) + amount,
        int(row["points"]) + amount,
        f"+{amount}",
    )
    await point_result(update, context, row, values, f"+{amount}")


async def takepoints_cmd(update, context):
    if not await rb.require_admin(update, context):
        return
    target, args = target_from_update(update, context.args)
    if target is None or not args:
        await update.effective_message.reply_text(point_admin_help(), parse_mode=ParseMode.HTML)
        return
    try:
        amount = int(args[0])
        if amount <= 0:
            raise ValueError
    except ValueError:
        await update.effective_message.reply_text("❌ Taškų kiekis turi būti teigiamas skaičius.")
        return
    row = find_user_row(target)
    if not row:
        await update.effective_message.reply_text("❌ Vartotojo DB neradau.")
        return
    values = adjust_points(
        update.effective_user.id,
        row,
        int(row["weekly_points"]) - amount,
        int(row["points"]) - amount,
        f"-{amount}",
    )
    await point_result(update, context, row, values, f"-{amount}")


async def setpoints_cmd(update, context):
    if not await rb.require_admin(update, context):
        return
    target, args = target_from_update(update, context.args)
    if target is None or len(args) < 2:
        await update.effective_message.reply_text(
            "Naudojimas: /setpoints @user SAVAITĖ VISO\nArba Reply: /setpoints 10 25"
        )
        return
    try:
        weekly = int(args[0])
        total = int(args[1])
        if weekly < 0 or total < 0:
            raise ValueError
    except ValueError:
        await update.effective_message.reply_text("❌ Reikia dviejų neneigiamų skaičių.")
        return
    row = find_user_row(target)
    if not row:
        await update.effective_message.reply_text("❌ Vartotojo DB neradau.")
        return
    values = adjust_points(update.effective_user.id, row, weekly, total, "SET")
    await point_result(update, context, row, values, "SET")


async def resetpoints_cmd(update, context):
    if not await rb.require_admin(update, context):
        return
    target, _args = target_from_update(update, context.args)
    if target is None:
        await update.effective_message.reply_text("Naudojimas: /resetpoints @user arba Reply /resetpoints")
        return
    row = find_user_row(target)
    if not row:
        await update.effective_message.reply_text("❌ Vartotojo DB neradau.")
        return
    values = adjust_points(update.effective_user.id, row, 0, 0, "RESET")
    await point_result(update, context, row, values, "RESET")


async def userinfo_cmd(update, context):
    if not await rb.require_admin(update, context):
        return
    target, _args = target_from_update(update, context.args)
    if target is None:
        await update.effective_message.reply_text("Naudojimas: /userinfo @user arba Reply /userinfo")
        return
    row = find_user_row(target)
    if not row:
        await update.effective_message.reply_text("❌ Vartotojo DB neradau.")
        return
    name = f"@{row['username']}" if row["username"] else f"ID {row['user_id']}"
    await update.effective_message.reply_text(
        f"{rb.em('brand')} <b>VARTOTOJAS</b>\n\n"
        f"{rb.em('group')} {rb.esc(name)}\n"
        f"ID: <code>{row['user_id']}</code>\n"
        f"{rb.em('crown')} Savaitė: <b>{row['weekly_points']}</b>\n"
        f"{rb.em('stats')} Viso: <b>{row['points']}</b>",
        parse_mode=ParseMode.HTML,
    )


async def pointhelp_cmd(update, context):
    if not await rb.require_admin(update, context):
        return
    await update.effective_message.reply_text(point_admin_help(), parse_mode=ParseMode.HTML)


# ---------------------------------------------------------------------------
# Branded handlers
# ---------------------------------------------------------------------------

async def start_cmd(update, context):
    """Keep the invite deep link and group behaviour; brand the private home."""
    if not update.effective_user:
        return
    if (
        update.effective_chat.type != ChatType.PRIVATE
        or (context.args and context.args[0].lower() == "invite")
    ):
        return await rb.start_cmd(update, context)
    rb.upsert_user(update.effective_user)
    is_admin = await rb.is_admin_user(update.effective_user.id, context)
    await update.effective_message.reply_text(
        home_text(),
        parse_mode=ParseMode.HTML,
        reply_markup=user_menu(is_admin),
    )


async def points_cmd(update, context):
    rb.upsert_user(update.effective_user)
    row = rb.get_user(update.effective_user.id)
    weekly = int(row["weekly_points"]) if row else 0
    total = int(row["points"]) if row else 0
    await update.effective_message.reply_text(
        f"{rb.em('trophy')} <b>MANO TAŠKAI</b>\n\n"
        f"{rb.em('crown')} <b>Šią savaitę:</b> {weekly}\n"
        f"{rb.em('stats')} <b>Iš viso:</b> {total}\n\n"
        f"{brand_line()}",
        parse_mode=ParseMode.HTML,
    )


async def how_cmd(update, context):
    text = (
        f"{rb.em('crown')} <b>KAIP VEIKIA</b>\n\n"
        f"{rb.em('invite')} Pasiimi savo invite ir daliniesi\n"
        f"{rb.em('group')} Naujas narys per tavo linką = <b>+1</b>\n"
        f"{rb.em('add')} Pats pridedi žmogų į grupę = <b>+1</b>\n"
        f"{rb.em('stats')} Abu būdai sumuojasi\n"
        f"{rb.em('trophy')} TOP reset: pirmadienį 00:00\n"
        "\n"
        f"{brand_line()}"
    )
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)


async def admin_cmd(update, context):
    if not await rb.require_admin(update, context):
        return
    await update.effective_message.reply_text(
        f"{rb.em('crown')} <b>ADMIN PANEL</b>\n\n"
        f"{rb.em('brand')} NĖRA DROPO valdymas",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_menu(),
    )


async def stats_cmd(update, context):
    if not await rb.require_admin(update, context):
        return
    with closing(rb.db()) as conn:
        users = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
        refs = conn.execute("SELECT COUNT(*) c FROM referrals").fetchone()["c"]
        week_points = conn.execute("SELECT COALESCE(SUM(weekly_points),0) c FROM users").fetchone()["c"]
    await update.effective_message.reply_text(
        f"{rb.em('stats')} <b>STATISTIKA</b>\n\n"
        f"{rb.em('group')} Nariai DB: <b>{users}</b>\n"
        f"{rb.em('invite')} Užskaityti invite / add: <b>{refs}</b>\n"
        f"{rb.em('trophy')} Savaitės taškai DB: <b>{week_points}</b>\n\n"
        f"{brand_line()}",
        parse_mode=ParseMode.HTML,
    )


async def adminnote_cmd(update, context):
    if not await rb.require_admin(update, context):
        return
    text = (
        f"{rb.em('crown')} <b>ADMIN KOMANDOS</b>\n\n"
        f"{rb.em('add')} /addpoints @user 5\n"
        f"{rb.em('stats')} /takepoints @user 2\n"
        f"{rb.em('trophy')} /setpoints @user 10 25\n"
        f"{rb.em('brand')} /resetpoints @user · /userinfo @user\n\n"
        f"{rb.em('share')} /adpack — ADS preview\n"
        f"{rb.em('invite')} /adsset konkursas promo — rotacija\n"
        f"{rb.em('stats')} /adsfast 15 · /adsinterval 30\n"
        f"{rb.em('share')} /adson · /adsoff · /adsnext · /ads\n"
        f"{rb.em('trophy')} /liveboard · /liveboardnew · /liveboardoff\n"
        f"{rb.em('stats')} /stats · /contestad · /promoad\n\n"
        f"{brand_line()}"
    )
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)


async def on_chat_member(update, context):
    change = update.chat_member
    if not change:
        return
    group_id = await rb.ensure_group(context)
    if change.chat.id != group_id:
        return
    joined = change.new_chat_member.user
    if joined.is_bot or not rb.became_member(change.old_chat_member.status, change.new_chat_member.status):
        return

    inviter_id = None
    source = None
    if change.invite_link:
        inviter_id = rb.owner_by_link(group_id, change.invite_link.invite_link)
        if inviter_id:
            source = "ref_link"
    if (
        inviter_id is None
        and not getattr(change, "via_join_request", False)
        and not getattr(change, "via_chat_folder_invite_link", False)
        and change.from_user
        and change.from_user.id != joined.id
        and not change.from_user.is_bot
    ):
        inviter_id = change.from_user.id
        source = "direct_add"
        rb.upsert_user(change.from_user)

    if not inviter_id or not rb.award_once(group_id, joined.id, inviter_id, source):
        return

    inviter = rb.get_user(inviter_id)
    try:
        await context.bot.send_message(
            chat_id=inviter_id,
            text=(
                f"{rb.em('trophy')} <b>+1 TAŠKAS</b>\n\n"
                f"{rb.em('crown')} Savaitė: {inviter['weekly_points']}\n"
                f"{rb.em('stats')} Iš viso: {inviter['points']}\n\n"
                f"{brand_line()}"
            ),
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        pass
    await rb.refresh_live_leaderboard(context.application)


async def weekly_reset_loop(application):
    while True:
        try:
            changed, _old_week, previous_top = rb.ensure_current_week()
            if changed:
                excluded = await rb.refresh_group_admin_ids(application)
                filtered = rb.filter_rows(previous_top, excluded, 3)
                if rb.WEEK_RESET_ANNOUNCE:
                    group_id = application.bot_data.get("group_id")
                    lines = [f"{rb.em('trophy')} <b>SAVAITĖ BAIGTA</b>", ""]
                    if filtered:
                        lines += rb.ranking_lines(filtered, "weekly_points", "tšk.")
                    lines += ["", f"{rb.em('stats')} Nauja savaitė prasidėjo.", brand_line()]
                    await application.bot.send_message(
                        chat_id=group_id,
                        text="\n".join(lines),
                        parse_mode=ParseMode.HTML,
                    )
                await rb.refresh_live_leaderboard(application)
        except Exception:
            rb.log.exception("Weekly reset klaida")
        await asyncio.sleep(60)


async def on_button(update, context):
    q = update.callback_query
    await q.answer()
    user = q.from_user
    rb.upsert_user(user)
    is_admin = await rb.is_admin_user(user.id, context)

    if q.data == "back":
        await q.edit_message_text(
            home_text(),
            parse_mode=ParseMode.HTML,
            reply_markup=user_menu(is_admin),
        )
        return

    if q.data == "my_link":
        link = await rb.ensure_personal_link(context, user)
        await q.edit_message_text(
            invite_text(link),
            parse_mode=ParseMode.HTML,
            reply_markup=rb.invite_markup(link),
            disable_web_page_preview=True,
        )
        return

    if q.data == "points":
        row = rb.get_user(user.id)
        await q.edit_message_text(
            f"{rb.em('trophy')} <b>MANO TAŠKAI</b>\n\n"
            f"{rb.em('crown')} Šią savaitę: <b>{row['weekly_points'] if row else 0}</b>\n"
            f"{rb.em('stats')} Iš viso: <b>{row['points'] if row else 0}</b>\n\n"
            f"{brand_line()}",
            parse_mode=ParseMode.HTML,
            reply_markup=user_menu(is_admin),
        )
        return

    excluded = await rb.refresh_group_admin_ids(context.application)

    if q.data == "top":
        await q.edit_message_text(weekly_top_text(excluded), parse_mode=ParseMode.HTML, reply_markup=user_menu(is_admin))
    elif q.data == "alltime":
        await q.edit_message_text(alltime_top_text(excluded), parse_mode=ParseMode.HTML, reply_markup=user_menu(is_admin))
    elif q.data == "lastweek":
        await q.edit_message_text(lastweek_top_text(excluded), parse_mode=ParseMode.HTML, reply_markup=user_menu(is_admin))
    elif q.data == "info":
        text = (
            f"{rb.em('crown')} <b>KAIP VEIKIA</b>\n\n"
            f"{rb.em('invite')} Invite → <b>+1</b>\n"
            f"{rb.em('add')} Add Member → <b>+1</b>\n"
            f"{rb.em('stats')} Abu būdai sumuojasi\n"
            f"{rb.em('trophy')} Reset: pirmadienį 00:00\n"
            "\n"
            f"{brand_line()}"
        )
        await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=user_menu(is_admin))
    elif q.data == "admin_panel" and is_admin:
        await q.edit_message_text(
            f"{rb.em('crown')} <b>ADMIN PANEL</b>\n\n{brand_line()}",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_menu(),
        )
    elif q.data.startswith("admin_") and is_admin:
        if q.data == "admin_ads_status":
            text = ads_status_text()
        elif q.data == "admin_ads_next":
            rb.set_setting("ads_force_send", "1")
            text = f"{rb.em('share')} ADS paleidžiamas."
        elif q.data == "admin_ads_on":
            rb.set_setting("ads_enabled", "1")
            rb.set_setting("ads_scheduler_initialized", "0")
            text = f"{rb.em('share')} AUTO ADS ON"
        elif q.data == "admin_ads_off":
            rb.set_setting("ads_enabled", "0")
            text = f"{rb.em('crown')} AUTO ADS OFF"
        elif q.data == "admin_live_refresh":
            mid = await rb.refresh_live_leaderboard(context.application)
            text = f"{rb.em('trophy')} TOP atnaujintas · {mid}"
        elif q.data == "admin_stats":
            with closing(rb.db()) as conn:
                users = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
                refs = conn.execute("SELECT COUNT(*) c FROM referrals").fetchone()["c"]
            text = f"{rb.em('stats')} Users: {users}\n{rb.em('invite')} Referrals: {refs}"
        elif q.data == "admin_points_help":
            text = point_admin_help()
        elif q.data == "admin_adpack":
            await rb.send_ad_preview(user.id, context)
            text = f"{rb.em('share')} ADS preview išsiųsti žemiau."
        else:
            text = f"{rb.em('crown')} /adminnote"
        await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=admin_menu())


# ---------------------------------------------------------------------------
# Command menus + live app (no message-count leaderboard)
# ---------------------------------------------------------------------------

async def set_command_menus(application):
    user_commands = [
        BotCommand("start", "Atidaryti NĖRA DROPO meniu"),
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


def main():
    ensure_adjustment_table()

    # Patch visible helpers used by core handlers/post_init.
    rb.user_menu = user_menu
    rb.admin_menu = admin_menu
    rb.weekly_top_text = weekly_top_text
    rb.alltime_top_text = alltime_top_text
    rb.lastweek_top_text = lastweek_top_text
    rb.live_top_text = live_top_text
    rb.invite_text = invite_text
    rb.contest_rose_caption = contest_rose_caption
    rb.promo_rose_caption = promo_rose_caption
    rb.ads_status_text = ads_status_text
    rb.weekly_reset_loop = weekly_reset_loop
    rb.set_command_menus = set_command_menus

    rb.init_db()
    app = (
        Application.builder()
        .token(rb.BOT_TOKEN)
        .post_init(rb.post_init)
        .post_shutdown(rb.post_shutdown)
        .build()
    )

    handlers = [
        ("start", start_cmd),
        ("mylink", rb.mylink_cmd),
        ("points", points_cmd),
        ("top", rb.top_cmd),
        ("leaderboard", rb.top_cmd),
        ("top10", rb.top_cmd),
        ("alltime", rb.alltime_cmd),
        ("lastweek", rb.lastweek_cmd),
        ("how", how_cmd),
        ("kaipveikia", how_cmd),
        ("admin", admin_cmd),
        ("stats", stats_cmd),
        ("addpoints", addpoints_cmd),
        ("takepoints", takepoints_cmd),
        ("removepoints", takepoints_cmd),
        ("setpoints", setpoints_cmd),
        ("resetpoints", resetpoints_cmd),
        ("userinfo", userinfo_cmd),
        ("pointhelp", pointhelp_cmd),
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
        ("adminnote", adminnote_cmd),
    ]
    for name, handler in handlers:
        app.add_handler(CommandHandler(name, handler))

    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(ChatMemberHandler(on_chat_member, ChatMemberHandler.CHAT_MEMBER))

    # Intentionally NO group-message counter / MessageHandler.
    print("NĖRA DROPO LIVE sistema paleista.")
    print(f"Grupė: {rb.GROUP_CHAT_RAW}")
    print("Žinučių TOP: išjungtas")
    print(f"Savaitės reset: pirmadienį 00:00 ({rb.WEEK_TIMEZONE})")

    app.run_polling(
        allowed_updates=["message", "callback_query", "chat_member", "my_chat_member"],
        drop_pending_updates=False,
    )


if __name__ == "__main__":
    main()
