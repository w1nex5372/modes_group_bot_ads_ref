"""NERA DROPO branded runtime layer.

Keeps referral_bot.py as the core and applies the NERA DROPO custom-emoji
presentation layer before starting the application. Inline keyboard buttons
still use regular Unicode emoji because Telegram does not support custom emoji
entities inside button labels.
"""

from contextlib import closing

from telegram.constants import ParseMode

import referral_bot as rb


# ---------------------------------------------------------------------------
# Branded text helpers
# ---------------------------------------------------------------------------

def brand_line():
    return f"{rb.em('brand')} <b>NĖRA DROPO</b>"


def weekly_top_text(excluded=None):
    rows = rb.weekly_top(exclude_ids=excluded)
    lines = [f"{rb.em('trophy')} <b>SAVAITĖS INVITE TOP 10</b>", ""]
    lines += rb.ranking_lines(rows, "weekly_points", "tšk.") if rows else [
        f"{rb.em('crown')} Kol kas TOP tuščias — būk pirmas."
    ]
    lines += [
        "",
        f"{rb.em('stats')} Reset: pirmadienį 00:00",
        brand_line(),
    ]
    return "\n".join(lines)


def message_top_text(group_id, excluded=None):
    rows = rb.weekly_message_top(group_id, exclude_ids=excluded)
    lines = [f"{rb.em('group')} <b>SAVAITĖS ŽINUČIŲ TOP 10</b>", ""]
    lines += rb.ranking_lines(rows, "message_count", "žin.") if rows else [
        f"{rb.em('crown')} Kol kas TOP tuščias."
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
        f"{rb.em('crown')} Kol kas TOP tuščias."
    ]
    lines += ["", brand_line()]
    return "\n".join(lines)


def lastweek_top_text(excluded=None):
    week_key, rows = rb.last_week_top(exclude_ids=excluded)
    if not rows:
        return (
            f"{rb.em('trophy')} <b>PRAEITA SAVAITĖ</b>\n\n"
            f"{rb.em('stats')} Rezultatų dar nėra.\n\n"
            f"{brand_line()}"
        )
    lines = [
        f"{rb.em('trophy')} <b>PRAEITA SAVAITĖ · {rb.esc(week_key)}</b>",
        "",
    ]
    lines += rb.ranking_lines(rows, "points", "tšk.")
    lines += ["", brand_line()]
    return "\n".join(lines)


def live_top_text(excluded=None):
    rows = rb.weekly_top(exclude_ids=excluded)
    lines = [f"{rb.em('trophy')} <b>SAVAITĖS INVITE TOP 10 · LIVE</b>", ""]
    lines += rb.ranking_lines(rows, "weekly_points", "tšk.") if rows else [
        f"{rb.em('crown')} Būk pirmas."
    ]
    lines += [
        "",
        f"{rb.em('stats')} Reset: pirmadienį 00:00",
        f"{rb.em('brand')} Atnaujinta {rb.local_now():%H:%M}",
        brand_line(),
    ]
    return "\n".join(lines)


def invite_text(link):
    return (
        f"{rb.em('invite')} <b>TAVO INVITE</b>\n\n"
        f"{rb.esc(link)}\n\n"
        f"{rb.em('trophy')} <b>+1 taškas</b> kai:\n"
        f"{rb.em('invite')} žmogus ateina per tavo nuorodą\n"
        f"{rb.em('add')} pats žmogų pridedi į grupę\n\n"
        f"{rb.em('crown')} Tas pats žmogus skaičiuojamas 1 kartą.\n\n"
        f"{brand_line()}"
    )


def ads_status_text():
    import json

    try:
        notes = json.loads(rb.get_setting("ads_notes", "[]"))
    except Exception:
        notes = []
    state = "ON" if rb.get_setting("ads_enabled", "0") == "1" else "OFF"
    return (
        f"{rb.em('brand')} <b>AUTO ADS</b>\n\n"
        f"{rb.em('crown')} Būsena: <b>{state}</b>\n"
        f"{rb.em('stats')} FAST: kas {rb.esc(rb.get_setting('ads_fast_interval_minutes','15'))} min.\n"
        f"{rb.em('stats')} NORMAL: kas {rb.esc(rb.get_setting('ads_interval_minutes','30'))} min.\n"
        f"{rb.em('invite')} Notes: {rb.esc(', '.join(notes) if notes else '—')}\n"
        f"{rb.em('share')} Paskutinis: {rb.esc(rb.get_setting('ads_last_note','—') or '—')}\n"
        f"{rb.em('trophy')} Rezultatas: {rb.esc(rb.get_setting('ads_last_result','—') or '—')}\n\n"
        f"{brand_line()}"
    )


# ---------------------------------------------------------------------------
# Branded command handlers
# ---------------------------------------------------------------------------

async def points_cmd(update, context):
    rb.upsert_user(update.effective_user)
    row = rb.get_user(update.effective_user.id)
    weekly = int(row["weekly_points"]) if row else 0
    total = int(row["points"]) if row else 0
    await update.effective_message.reply_text(
        f"{rb.em('trophy')} <b>MANO TAŠKAI</b>\n\n"
        f"{rb.em('crown')} Šią savaitę: <b>{weekly}</b>\n"
        f"{rb.em('stats')} Iš viso: <b>{total}</b>\n\n"
        f"{brand_line()}",
        parse_mode=ParseMode.HTML,
    )


async def how_cmd(update, context):
    text = (
        f"{rb.em('crown')} <b>KAIP VEIKIA</b>\n\n"
        f"{rb.em('invite')} Pasiimi savo invite nuorodą\n"
        f"{rb.em('share')} Daliniesi draugams / grupėms\n"
        f"{rb.em('group')} Naujas narys = <b>+1</b>\n"
        f"{rb.em('add')} Add Member = <b>+1</b>\n"
        f"{rb.em('trophy')} TOP reset: pirmadienį 00:00\n"
        f"{rb.em('crown')} Grupės adminai TOP'e nerodomi\n\n"
        f"{brand_line()}"
    )
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)


async def admin_cmd(update, context):
    if not await rb.require_admin(update, context):
        return
    await update.effective_message.reply_text(
        f"{rb.em('crown')} <b>ADMIN PANEL</b>\n\n{brand_line()}",
        parse_mode=ParseMode.HTML,
        reply_markup=rb.admin_menu(),
    )


async def stats_cmd(update, context):
    if not await rb.require_admin(update, context):
        return
    group_id = await rb.ensure_group(context)
    with closing(rb.db()) as conn:
        users = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
        refs = conn.execute("SELECT COUNT(*) c FROM referrals").fetchone()["c"]
        msgs = conn.execute(
            "SELECT COALESCE(SUM(message_count),0) c FROM message_stats WHERE group_id=? AND week_key=?",
            (group_id, rb.current_week_key()),
        ).fetchone()["c"]
    await update.effective_message.reply_text(
        f"{rb.em('stats')} <b>STATISTIKA</b>\n\n"
        f"{rb.em('group')} Nariai DB: <b>{users}</b>\n"
        f"{rb.em('invite')} Invite / Add: <b>{refs}</b>\n"
        f"{rb.em('share')} Savaitės žinutės: <b>{msgs}</b>\n\n"
        f"{brand_line()}",
        parse_mode=ParseMode.HTML,
    )


async def adminnote_cmd(update, context):
    if not await rb.require_admin(update, context):
        return
    text = (
        f"{rb.em('crown')} <b>ADMIN KOMANDOS</b>\n\n"
        f"{rb.em('brand')} /adpack — ADS preview su image\n"
        f"{rb.em('invite')} /adsset konkursas promo — rotacija\n"
        f"{rb.em('stats')} /adsfast 15 — FAST intervalas\n"
        f"{rb.em('stats')} /adsinterval 30 — NORMAL intervalas\n"
        f"{rb.em('share')} /adsnext — paleisti dabar\n"
        f"{rb.em('trophy')} /liveboard — atnaujinti LIVE TOP\n"
        f"{rb.em('stats')} /stats — statistika\n\n"
        f"{brand_line()}"
    )
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)


# Intercept the callback texts that were still using generic emoji.
_original_on_button = rb.on_button


async def on_button(update, context):
    q = update.callback_query
    data = q.data

    if data not in {"points", "info", "admin_panel"}:
        return await _original_on_button(update, context)

    await q.answer()
    user = q.from_user
    rb.upsert_user(user)
    is_admin = await rb.is_admin_user(user.id, context)

    if data == "points":
        row = rb.get_user(user.id)
        weekly = int(row["weekly_points"]) if row else 0
        total = int(row["points"]) if row else 0
        text = (
            f"{rb.em('trophy')} <b>MANO TAŠKAI</b>\n\n"
            f"{rb.em('crown')} Šią savaitę: <b>{weekly}</b>\n"
            f"{rb.em('stats')} Iš viso: <b>{total}</b>\n\n"
            f"{brand_line()}"
        )
        await q.edit_message_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=rb.user_menu(is_admin),
        )
        return

    if data == "info":
        text = (
            f"{rb.em('crown')} <b>KAIP VEIKIA</b>\n\n"
            f"{rb.em('invite')} Invite nuoroda = <b>+1</b>\n"
            f"{rb.em('add')} Add Member = <b>+1</b>\n"
            f"{rb.em('trophy')} Reset: pirmadienį 00:00\n"
            f"{rb.em('crown')} Adminai TOP'e nerodomi\n\n"
            f"{brand_line()}"
        )
        await q.edit_message_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=rb.user_menu(is_admin),
        )
        return

    if data == "admin_panel" and is_admin:
        await q.edit_message_text(
            f"{rb.em('crown')} <b>ADMIN PANEL</b>\n\n{brand_line()}",
            parse_mode=ParseMode.HTML,
            reply_markup=rb.admin_menu(),
        )


# ---------------------------------------------------------------------------
# Apply patch and launch
# ---------------------------------------------------------------------------

def apply_branding():
    rb.weekly_top_text = weekly_top_text
    rb.message_top_text = message_top_text
    rb.alltime_top_text = alltime_top_text
    rb.lastweek_top_text = lastweek_top_text
    rb.live_top_text = live_top_text
    rb.invite_text = invite_text
    rb.ads_status_text = ads_status_text

    rb.points_cmd = points_cmd
    rb.how_cmd = how_cmd
    rb.admin_cmd = admin_cmd
    rb.stats_cmd = stats_cmd
    rb.adminnote_cmd = adminnote_cmd
    rb.on_button = on_button


if __name__ == "__main__":
    apply_branding()
    rb.main()
