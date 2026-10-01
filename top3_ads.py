"""Current-week invite TOP 3 ad; reads live points at send time."""

import html
import sqlite3
from contextlib import closing

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def top3_rows(db_path, excluded_ids=()):
    excluded = {int(item) for item in excluded_ids}
    with closing(sqlite3.connect(db_path, timeout=30)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT user_id,username,first_name,weekly_points FROM users
               WHERE weekly_points>0 ORDER BY weekly_points DESC,user_id ASC"""
        ).fetchall()
    return [dict(row) for row in rows if row["user_id"] not in excluded][:3]


def render_top3(rows, brand="JACKIE CHAN"):
    if not rows:
        return None
    medals = ("🥇", "🥈", "🥉")
    lines = [f"🏆 <b>{html.escape(brand)} · SAVAITĖS TOP 3</b>", ""]
    for index, row in enumerate(rows):
        name = row.get("username")
        display = "@" + html.escape(name) if name else html.escape(row.get("first_name") or "Narys")
        lines.append(f"{medals[index]} {display} — <b>{int(row['weekly_points'])}</b> tšk.")
    lines.extend([
        "",
        "🥋 Pakviesk draugą arba pridėk narį ir kilk į TOP!",
        "🔄 Naujas raundas pirmadienį 00:00.",
        "<i>Neoficiali bendruomenė · nesusijusi su Jackie Chanu.</i>",
    ])
    return "\n".join(lines)


def invite_markup(bot_username):
    username = str(bot_username or "").strip().lstrip("@")
    if not username:
        return None
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🔗 GAUTI MANO INVITE", url=f"https://t.me/{username}?start=invite")
    ]])
