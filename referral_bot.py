import asyncio
import json
import logging
import os
import re
import sqlite3
from contextlib import closing, suppress
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from telegram import (
    BotCommand,
    BotCommandScopeChatAdministrators,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ChatMemberStatus
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
)

load_dotenv()

BOT_TOKEN = os.environ["BOT_TOKEN"].strip()
GROUP_CHAT_RAW = os.getenv(
    "GROUP",
    os.getenv("GROUP_CHAT", "@NERADAUDROPO"),
).strip()

GROUP_PUBLIC_URL = os.getenv("GROUP_PUBLIC_URL", "").strip()
if not GROUP_PUBLIC_URL and GROUP_CHAT_RAW.startswith("@"):
    GROUP_PUBLIC_URL = "https://t.me/" + GROUP_CHAT_RAW[1:]

DB_PATH = os.getenv("DB_PATH", "referrals.sqlite3").strip()
WEEK_TIMEZONE = os.getenv("WEEK_TIMEZONE", "Europe/Vilnius").strip()
WEEK_RESET_ANNOUNCE = os.getenv("WEEK_RESET_ANNOUNCE", "true").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
LIVE_LEADERBOARD_ENABLED = os.getenv(
    "LIVE_LEADERBOARD_ENABLED",
    "true",
).lower() in {"1", "true", "yes", "on"}
LIVE_LEADERBOARD_REFRESH_SECONDS = max(
    15,
    int(os.getenv("LIVE_LEADERBOARD_REFRESH_SECONDS", "60")),
)
ADMIN_IDS = {
    int(item.strip())
    for item in os.getenv("ADMIN_IDS", "").split(",")
    if item.strip().isdigit()
}

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("referral_bot")


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def local_now():
    return datetime.now(ZoneInfo(WEEK_TIMEZONE))


def current_week_key():
    iso = local_now().isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def init_db():
    with closing(db()) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                points INTEGER NOT NULL DEFAULT 0,
                weekly_points INTEGER NOT NULL DEFAULT 0,
                invite_link TEXT UNIQUE,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS invite_links (
                group_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                invite_link TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                PRIMARY KEY (group_id, user_id)
            );

            CREATE INDEX IF NOT EXISTS idx_invite_links_link
            ON invite_links(invite_link);

            CREATE TABLE IF NOT EXISTS referrals (
                group_id INTEGER NOT NULL,
                joined_user_id INTEGER NOT NULL,
                inviter_user_id INTEGER NOT NULL,
                source TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (group_id, joined_user_id)
            );

            CREATE INDEX IF NOT EXISTS idx_ref_inviter
            ON referrals(inviter_user_id);

            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS weekly_history (
                week_key TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                username TEXT,
                first_name TEXT,
                points INTEGER NOT NULL,
                saved_at TEXT NOT NULL,
                PRIMARY KEY (week_key, user_id)
            );

            CREATE INDEX IF NOT EXISTS idx_weekly_history_week_points
            ON weekly_history(week_key, points DESC);

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )

        cols = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(users)").fetchall()
        }
        if "weekly_points" not in cols:
            conn.execute(
                "ALTER TABLE users "
                "ADD COLUMN weekly_points INTEGER NOT NULL DEFAULT 0"
            )

        defaults = {
            "ads_enabled": "0",
            "ads_interval_minutes": "30",
            "ads_notes": json.dumps(["ads"], ensure_ascii=False),
            "ads_next_index": "0",
            "ads_last_sent_ts": "0",
            "ads_force_send": "0",
            "live_leaderboard_enabled": "1",
        }
        for key, value in defaults.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings(key, value) VALUES(?, ?)",
                (key, value),
            )

        conn.commit()

    ensure_current_week()


def get_setting(key: str, default=None):
    with closing(db()) as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key=?",
            (key,),
        ).fetchone()
        return row["value"] if row else default


def set_setting(key: str, value):
    with closing(db()) as conn:
        conn.execute(
            """
            INSERT INTO settings(key, value)
            VALUES(?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (key, str(value)),
        )
        conn.commit()


def ensure_current_week():
    week_key = current_week_key()

    with closing(db()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT value FROM meta WHERE key='current_week'"
        ).fetchone()

        if row is None:
            conn.execute(
                "INSERT INTO meta(key, value) VALUES('current_week', ?)",
                (week_key,),
            )
            conn.commit()
            return False, None, []

        old_week = row["value"]
        if old_week == week_key:
            conn.commit()
            return False, None, []

        previous_top = conn.execute(
            """
            SELECT user_id, username, first_name, weekly_points
            FROM users
            WHERE weekly_points > 0
            ORDER BY weekly_points DESC, user_id ASC
            LIMIT 10
            """
        ).fetchall()

        conn.execute(
            """
            INSERT OR REPLACE INTO weekly_history
            (week_key, user_id, username, first_name, points, saved_at)
            SELECT ?, user_id, username, first_name, weekly_points, ?
            FROM users
            WHERE weekly_points > 0
            """,
            (old_week, now_iso()),
        )

        conn.execute("UPDATE users SET weekly_points = 0")
        conn.execute(
            "UPDATE meta SET value=? WHERE key='current_week'",
            (week_key,),
        )
        conn.commit()

        return True, old_week, previous_top


def upsert_user(user):
    if not user:
        return

    ensure_current_week()

    with closing(db()) as conn:
        conn.execute(
            """
            INSERT INTO users
            (user_id, username, first_name, points, weekly_points, created_at)
            VALUES (?, ?, ?, 0, 0, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username=excluded.username,
                first_name=excluded.first_name
            """,
            (user.id, user.username, user.first_name, now_iso()),
        )
        conn.commit()


def get_user(user_id: int):
    ensure_current_week()
    with closing(db()) as conn:
        return conn.execute(
            "SELECT * FROM users WHERE user_id=?",
            (user_id,),
        ).fetchone()


def get_personal_link(group_id: int, user_id: int):
    with closing(db()) as conn:
        row = conn.execute(
            """
            SELECT invite_link
            FROM invite_links
            WHERE group_id=? AND user_id=?
            """,
            (group_id, user_id),
        ).fetchone()
        return row["invite_link"] if row else None


def save_personal_link(group_id: int, user_id: int, link: str):
    with closing(db()) as conn:
        conn.execute(
            """
            INSERT INTO invite_links(group_id, user_id, invite_link, created_at)
            VALUES(?, ?, ?, ?)
            ON CONFLICT(group_id, user_id) DO UPDATE SET
                invite_link=excluded.invite_link,
                created_at=excluded.created_at
            """,
            (group_id, user_id, link, now_iso()),
        )
        conn.commit()


def owner_by_link(group_id: int, link: str):
    with closing(db()) as conn:
        row = conn.execute(
            """
            SELECT user_id
            FROM invite_links
            WHERE group_id=? AND invite_link=?
            """,
            (group_id, link),
        ).fetchone()
        return row["user_id"] if row else None


def award_once(group_id: int, joined_user_id: int, inviter_user_id: int, source: str) -> bool:
    if joined_user_id == inviter_user_id:
        return False

    ensure_current_week()

    with closing(db()) as conn:
        try:
            conn.execute(
                """
                INSERT INTO referrals
                (group_id, joined_user_id, inviter_user_id, source, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (group_id, joined_user_id, inviter_user_id, source, now_iso()),
            )

            conn.execute(
                """
                INSERT INTO users
                (user_id, username, first_name, points, weekly_points, created_at)
                VALUES (?, NULL, NULL, 1, 1, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    points = points + 1,
                    weekly_points = weekly_points + 1
                """,
                (inviter_user_id, now_iso()),
            )

            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False


# ---------------------------------------------------------------------------
# Leaderboards
# ---------------------------------------------------------------------------

def weekly_top(limit=10):
    ensure_current_week()
    with closing(db()) as conn:
        return conn.execute(
            """
            SELECT user_id, username, first_name, weekly_points
            FROM users
            WHERE weekly_points > 0
            ORDER BY weekly_points DESC, user_id ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()


def alltime_top(limit=10):
    with closing(db()) as conn:
        return conn.execute(
            """
            SELECT user_id, username, first_name, points
            FROM users
            WHERE points > 0
            ORDER BY points DESC, user_id ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()


def last_week_top(limit=10):
    ensure_current_week()
    with closing(db()) as conn:
        row = conn.execute(
            """
            SELECT week_key
            FROM weekly_history
            ORDER BY saved_at DESC
            LIMIT 1
            """
        ).fetchone()
        if not row:
            return None, []

        week_key = row["week_key"]
        rows = conn.execute(
            """
            SELECT user_id, username, first_name, points
            FROM weekly_history
            WHERE week_key=?
            ORDER BY points DESC, user_id ASC
            LIMIT ?
            """,
            (week_key, limit),
        ).fetchall()
        return week_key, rows


def display_name(row):
    if row["username"]:
        return f"@{row['username']}"
    if row["first_name"]:
        return row["first_name"]
    return f"ID {row['user_id']}"


def weekly_top_text():
    rows = weekly_top()
    lines = [f"🏆 SAVAITĖS TOP — {current_week_key()}"]
    if not rows:
        lines.extend(["", "Kol kas taškų nėra."])
    else:
        lines.append("")
        medals = ["🥇", "🥈", "🥉"]
        for index, row in enumerate(rows, 1):
            prefix = medals[index - 1] if index <= 3 else f"{index}."
            lines.append(f"{prefix} {display_name(row)} — {row['weekly_points']} tšk.")
    lines.extend(["", "🔄 Reset: pirmadienį 00:00"])
    return "\n".join(lines)


def alltime_top_text():
    rows = alltime_top()
    lines = ["📊 VISO LAIKO TOP"]
    if not rows:
        lines.extend(["", "Kol kas taškų nėra."])
    else:
        lines.append("")
        for index, row in enumerate(rows, 1):
            lines.append(f"{index}. {display_name(row)} — {row['points']} tšk.")
    return "\n".join(lines)


def last_week_top_text():
    week_key, rows = last_week_top()
    if not rows:
        return "🥇 Praėjusios savaitės rezultatų dar nėra."

    lines = [f"🥇 PRAĖJUSI SAVAITĖ — {week_key}", ""]
    for index, row in enumerate(rows, 1):
        lines.append(f"{index}. {display_name(row)} — {row['points']} tšk.")
    return "\n".join(lines)


def live_leaderboard_text():
    rows = weekly_top(10)
    lines = ["🏆 SAVAITĖS INVITE TOP 10 — LIVE", ""]
    if not rows:
        lines.append("Kol kas TOP tuščias. Būk pirmas 👀")
    else:
        medals = ["🥇", "🥈", "🥉"]
        for index, row in enumerate(rows, 1):
            prefix = medals[index - 1] if index <= 3 else f"{index}."
            lines.append(f"{prefix} {display_name(row)} — {row['weekly_points']} tšk.")
    lines.extend(["", "🔄 Reset: pirmadienį 00:00", f"🟢 Atnaujinta: {local_now():%H:%M}"])
    return "\n".join(lines)


def group_button_text(application: Application):
    title = str(application.bot_data.get("group_title", "GRUPĖ")).strip() or "GRUPĖ"
    if len(title) > 28:
        title = title[:27] + "…"
    return f"👥 {title}"


def live_leaderboard_markup(application: Application):
    username = application.bot_data.get("bot_username")
    buttons = []
    if username:
        buttons.append([InlineKeyboardButton("🎯 DALYVAUTI / GAUTI INVITE", url=f"https://t.me/{username}?start=invite")])
    if GROUP_PUBLIC_URL:
        buttons.append([InlineKeyboardButton(group_button_text(application), url=GROUP_PUBLIC_URL)])
    return InlineKeyboardMarkup(buttons) if buttons else None


async def refresh_live_leaderboard(application: Application, force_new: bool = False):
    if get_setting("live_leaderboard_enabled", "1" if LIVE_LEADERBOARD_ENABLED else "0") != "1":
        return None

    group_id = application.bot_data.get("group_id")
    if not group_id:
        return None

    lock = application.bot_data.setdefault("_leaderboard_lock", asyncio.Lock())
    async with lock:
        key = f"live_leaderboard_message_id:{group_id}"
        raw = get_setting(key, "")
        try:
            message_id = int(raw) if raw else None
        except ValueError:
            message_id = None

        text = live_leaderboard_text()
        markup = live_leaderboard_markup(application)

        if message_id and not force_new:
            try:
                await application.bot.edit_message_text(chat_id=group_id, message_id=message_id, text=text, reply_markup=markup)
                return message_id
            except Exception as exc:
                if "not modified" in str(exc).lower():
                    return message_id
                log.warning("LIVE TOP edit nepavyko; kuriamas naujas: %s", exc)

        message = await application.bot.send_message(chat_id=group_id, text=text, reply_markup=markup)
        set_setting(key, str(message.message_id))
        return message.message_id


async def live_leaderboard_loop(application: Application):
    while True:
        await asyncio.sleep(LIVE_LEADERBOARD_REFRESH_SECONDS)
        try:
            await refresh_live_leaderboard(application)
        except Exception:
            log.exception("LIVE TOP 10 atnaujinimo klaida")


# ---------------------------------------------------------------------------
# Menus and permissions
# ---------------------------------------------------------------------------

async def ensure_group(context: ContextTypes.DEFAULT_TYPE):
    if "group_id" not in context.application.bot_data:
        chat = await context.bot.get_chat(GROUP_CHAT_RAW)
        context.application.bot_data["group_id"] = chat.id
        context.application.bot_data["group_title"] = chat.title or str(chat.id)
    return context.application.bot_data["group_id"]


async def is_admin_user(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if user_id in ADMIN_IDS:
        return True
    try:
        group_id = await ensure_group(context)
        member = await context.bot.get_chat_member(group_id, user_id)
        return member.status in {ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER}
    except Exception:
        return False


async def require_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user = update.effective_user
    if not user:
        return False
    ok = await is_admin_user(user.id, context)
    if not ok and update.effective_message:
        await update.effective_message.reply_text("⛔ Ši komanda skirta grupės administratoriui.")
    return ok


def user_menu(is_admin=False):
    rows = [
        [InlineKeyboardButton("🔗 Mano invite", callback_data="my_link"), InlineKeyboardButton("⭐ Mano taškai", callback_data="points")],
        [InlineKeyboardButton("🏆 Savaitės TOP", callback_data="top"), InlineKeyboardButton("📊 Viso TOP", callback_data="alltime")],
        [InlineKeyboardButton("🥇 Praeita savaitė", callback_data="lastweek"), InlineKeyboardButton("📋 Komandos", callback_data="commands")],
        [InlineKeyboardButton("ℹ️ Kaip veikia", callback_data="info")],
    ]
    if is_admin:
        rows.append([InlineKeyboardButton("🛠 ADMIN PANEL", callback_data="admin_panel")])
    return InlineKeyboardMarkup(rows)


def admin_panel_markup():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 ADS status", callback_data="admin_ads_status"), InlineKeyboardButton("⏭ Kitas ADS", callback_data="admin_ads_next")],
        [InlineKeyboardButton("🟢 ADS ON", callback_data="admin_ads_on"), InlineKeyboardButton("🔴 ADS OFF", callback_data="admin_ads_off")],
        [InlineKeyboardButton("🏆 Atnaujinti TOP", callback_data="admin_live_refresh"), InlineKeyboardButton("📊 Statistika", callback_data="admin_stats")],
        [InlineKeyboardButton("📄 Konkursas ADS", callback_data="admin_contest_ad"), InlineKeyboardButton("📋 Admin komandos", callback_data="admin_commands")],
        [InlineKeyboardButton("⬅️ Atgal", callback_data="back")],
    ])


def user_commands_text():
    return (
        "📋 VARTOTOJO KOMANDOS\n\n"
        "/mylink — tavo asmeninė invite nuoroda\n"
        "/points — tavo savaitės ir bendri taškai\n"
        "/top — šios savaitės TOP 10\n"
        "/alltime — viso laiko TOP 10\n"
        "/lastweek — praėjusios savaitės TOP\n"
        "/help — šis komandų sąrašas\n"
        "/how — kaip veikia konkursas"
    )


def how_it_works_text():
    return (
        "ℹ️ KAIP VEIKIA INVITE KONKURSAS\n\n"
        "1. Bote pasiimi savo asmeninę invite nuorodą.\n"
        "2. Daliniesi ja su draugais.\n"
        "3. Naujas žmogus prisijungia — gauni +1 tašką.\n"
        "4. Tiesioginis Add Member taip pat gali duoti +1 tam, kas žmogų pridėjo.\n"
        "5. Tas pats žmogus toje pačioje grupėje užskaitomas tik kartą.\n"
        "6. Savaitės TOP resetinamas pirmadienį 00:00.\n"
        "7. Viso laiko taškai nedingsta."
    )


def admin_commands_text():
    return (
        "🛠 ADMIN KOMANDOS\n\n"
        "BENDRA:\n/admin — atidaryti admin panelę\n/stats — sistemos statistika\n\n"
        "LIVE TOP:\n/liveboard — įjungti / atnaujinti LIVE TOP\n/liveboardnew — sukurti naują LIVE TOP postą\n/liveboardoff — išjungti LIVE TOP auto update\n/topad — papildomas TOP postas\n\n"
        "ROSE ADS:\n/ads — ADS statusas\n/adsset ads konkursas promo — note eilė\n/adsinterval 10 — intervalas minutėmis\n/adson — įjungti\n/adsoff — išjungti\n/adsnext — kitas ADS dabar\n\n"
        "PARUOŠTI TEKSTAI:\n/contestad — konkursas Rose note tekstas\n/adminnote — šis admin cheat-sheet"
    )


def stats_text():
    ensure_current_week()
    with closing(db()) as conn:
        total_users = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
        total_refs = conn.execute("SELECT COUNT(*) AS c FROM referrals").fetchone()["c"]
        week_refs = conn.execute("SELECT COALESCE(SUM(weekly_points), 0) AS c FROM users").fetchone()["c"]
    return (
        "📊 SISTEMOS STATISTIKA\n\n"
        f"Vartotojų DB: {total_users}\n"
        f"Visų laikų invite/add: {total_refs}\n"
        f"Šios savaitės taškai: {week_refs}\n"
        f"Savaitė: {current_week_key()}"
    )


# ---------------------------------------------------------------------------
# Invite links and prepared ad text
# ---------------------------------------------------------------------------

async def ensure_personal_link(context: ContextTypes.DEFAULT_TYPE, user):
    upsert_user(user)
    group_id = await ensure_group(context)
    existing = get_personal_link(group_id, user.id)
    if existing:
        return existing

    link_obj = await context.bot.create_chat_invite_link(chat_id=group_id, name=f"ref_{user.id}"[:32])
    save_personal_link(group_id, user.id, link_obj.invite_link)
    return link_obj.invite_link


def bot_private_url(application: Application, start_param=None):
    username = application.bot_data.get("bot_username")
    if not username:
        return None
    base = f"https://t.me/{username}"
    return f"{base}?start={start_param}" if start_param else base


def contest_ad_text(application: Application):
    bot_url = bot_private_url(application, "invite") or "https://t.me/TAVO_BOTO_USERNAME"
    title = application.bot_data.get("group_title", "NĖRA DROPO")
    lines = [
        "🏆 SAVAITĖS INVITE KONKURSAS",
        "",
        "Nori pakilti į TOP? 👀",
        "",
        "🔗 1. Spausk „DALYVAUTI“",
        "🤖 2. Bote pasirink „Mano invite“",
        "👥 3. Dalinkis savo asmenine grupės nuoroda",
        "⭐ 4. Kiekvienas naujas narys = +1 taškas",
        "",
        "🏆 TOP 10 leaderboardas grupėje atsinaujina automatiškai.",
        "🔄 Kiekvieną pirmadienį 00:00 savaitės taškai resetinami ir prasideda naujas etapas.",
        "",
        "⚠️ Tas pats žmogus užskaitomas tik vieną kartą.",
        "",
        f"[🎯 DALYVAUTI](buttonurl://{bot_url})",
    ]
    if GROUP_PUBLIC_URL:
        lines.append(f"[🏆 {title}](buttonurl://{GROUP_PUBLIC_URL}:same)")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# User/admin commands and callbacks
# ---------------------------------------------------------------------------

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return

    upsert_user(user)
    await ensure_group(context)
    is_admin = await is_admin_user(user.id, context)

    if context.args and context.args[0].lower() == "invite":
        try:
            link = await ensure_personal_link(context, user)
            text = "🔗 Tavo asmeninė invite nuoroda:\n\n" + link + "\n\nKiekvienas naujas žmogus, pirmą kartą prisijungęs per ją, = +1 savaitės taškas."
        except Exception:
            log.exception("Nepavyko sukurti invite link")
            text = "Nepavyko sukurti invite nuorodos. Patikrink boto admin teises grupėje."
    else:
        title = context.application.bot_data.get("group_title", "grupė")
        text = f"👋 {title}\n\nPasirink veiksmą apačioje."

    await update.effective_message.reply_text(text, reply_markup=user_menu(is_admin))


async def mylink_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return

    if update.effective_chat and update.effective_chat.id != user.id:
        url = bot_private_url(context.application, "invite")
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔗 GAUTI MANO INVITE", url=url)]]) if url else None
        await update.effective_message.reply_text("🔗 Asmeninę invite nuorodą pasiimk privačiame bote.", reply_markup=markup)
        return

    try:
        link = await ensure_personal_link(context, user)
        await update.effective_message.reply_text(f"🔗 Tavo invite nuoroda:\n\n{link}\n\nNaujas narys per ją = +1 taškas.")
    except Exception:
        log.exception("Invite link klaida")
        await update.effective_message.reply_text("Nepavyko sukurti nuorodos. Botui reikia admin teisės valdyti invite links.")


async def points_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    upsert_user(update.effective_user)
    row = get_user(update.effective_user.id)
    weekly = int(row["weekly_points"]) if row else 0
    total = int(row["points"]) if row else 0
    await update.effective_message.reply_text(f"⭐ Šią savaitę: {weekly} tšk.\n📊 Iš viso: {total} tšk.")


async def top_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(weekly_top_text())


async def alltime_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(alltime_top_text())


async def lastweek_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(last_week_top_text())


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(user_commands_text())


async def how_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(how_it_works_text())


async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        return
    await update.effective_message.reply_text("🛠 ADMIN PANEL\n\nPasirink veiksmą:", reply_markup=admin_panel_markup())


async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        return
    await update.effective_message.reply_text(stats_text())


async def contestad_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        return
    await update.effective_message.reply_text("📄 ROSE NOTE „konkursas“ — nukopijuok tekstą žemiau:\n\n" + contest_ad_text(context.application))


async def adminnote_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        return
    await update.effective_message.reply_text(admin_commands_text())


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    upsert_user(user)
    is_admin = await is_admin_user(user.id, context)
    menu = user_menu(is_admin)

    if query.data == "back":
        title = context.application.bot_data.get("group_title", "grupė")
        await query.edit_message_text(f"👋 {title}\n\nPasirink veiksmą apačioje.", reply_markup=menu)
        return

    if query.data == "my_link":
        try:
            link = await ensure_personal_link(context, user)
            text = f"🔗 Tavo asmeninė invite nuoroda:\n\n{link}\n\nKiekvienas naujas žmogus per ją = +1 savaitės taškas."
        except Exception:
            log.exception("Nepavyko sukurti invite link")
            text = "Nepavyko sukurti invite nuorodos. Patikrink boto admin teises grupėje."
        await query.edit_message_text(text, reply_markup=menu)
        return

    if query.data == "points":
        row = get_user(user.id)
        weekly = int(row["weekly_points"]) if row else 0
        total = int(row["points"]) if row else 0
        await query.edit_message_text(f"⭐ Šią savaitę: {weekly} tšk.\n📊 Iš viso: {total} tšk.", reply_markup=menu)
        return

    if query.data == "top":
        await query.edit_message_text(weekly_top_text(), reply_markup=menu)
        return
    if query.data == "alltime":
        await query.edit_message_text(alltime_top_text(), reply_markup=menu)
        return
    if query.data == "lastweek":
        await query.edit_message_text(last_week_top_text(), reply_markup=menu)
        return
    if query.data == "commands":
        await query.edit_message_text(user_commands_text(), reply_markup=menu)
        return
    if query.data == "info":
        await query.edit_message_text(how_it_works_text(), reply_markup=menu)
        return

    if query.data == "admin_panel":
        if not is_admin:
            await query.edit_message_text("⛔ Admin panelė skirta tik grupės administratoriams.", reply_markup=menu)
            return
        await query.edit_message_text("🛠 ADMIN PANEL\n\nPasirink veiksmą:", reply_markup=admin_panel_markup())
        return

    if query.data.startswith("admin_"):
        if not is_admin:
            await query.edit_message_text("⛔ Admin veiksmai skirti tik grupės administratoriams.", reply_markup=menu)
            return

        if query.data == "admin_ads_status":
            text = ads_status_text()
        elif query.data == "admin_ads_next":
            if get_setting("ads_enabled", "0") != "1":
                text = "ADS rotacija išjungta. Pirma įjunk ADS."
            else:
                set_setting("ads_force_send", "1")
                text = "⏭ Kitas ADS bus paleistas netrukus."
        elif query.data == "admin_ads_on":
            set_setting("ads_enabled", "1")
            set_setting("ads_force_send", "1")
            text = "🟢 ADS rotacija įjungta."
        elif query.data == "admin_ads_off":
            set_setting("ads_enabled", "0")
            set_setting("ads_force_send", "0")
            text = "🔴 ADS rotacija išjungta."
        elif query.data == "admin_live_refresh":
            message_id = await refresh_live_leaderboard(context.application)
            text = f"✅ LIVE TOP atnaujintas.\nMessage ID: {message_id}"
        elif query.data == "admin_stats":
            text = stats_text()
        elif query.data == "admin_contest_ad":
            text = "📄 ROSE NOTE „konkursas“:\n\n" + contest_ad_text(context.application)
        elif query.data == "admin_commands":
            text = admin_commands_text()
        else:
            text = "Nežinomas admin veiksmas."

        await query.edit_message_text(text, reply_markup=admin_panel_markup())


# ---------------------------------------------------------------------------
# Rose ADS settings controlled from the bot
# ---------------------------------------------------------------------------

def get_ads_notes():
    raw = get_setting("ads_notes", '["ads"]')
    try:
        notes = json.loads(raw)
    except Exception:
        notes = ["ads"]
    if not isinstance(notes, list):
        return ["ads"]
    clean = []
    for note in notes:
        note = str(note).strip()
        if note and note not in clean:
            clean.append(note)
    return clean


def ads_status_text():
    notes = get_ads_notes()
    enabled = get_setting("ads_enabled", "0") == "1"
    try:
        interval = int(get_setting("ads_interval_minutes", "30"))
    except ValueError:
        interval = 30
    try:
        index = int(get_setting("ads_next_index", "0"))
    except ValueError:
        index = 0
    next_note = notes[index % len(notes)] if notes else "—"
    return (
        "📢 ROSE ADS ROTACIJA\n\n"
        f"Būsena: {'🟢 ĮJUNGTA' if enabled else '🔴 IŠJUNGTA'}\n"
        f"Intervalas: kas {interval} min.\n"
        f"Rose notes: {', '.join(notes) if notes else 'nėra'}\n"
        f"Kitas: {next_note}\n\n"
        "Keisti:\n/adsset ads konkursas promo\n/adsinterval 10\n/adson\n/adsoff\n/adsnext"
    )


async def ads_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        return
    await update.effective_message.reply_text(ads_status_text())


async def adsset_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        return

    notes = []
    for raw in context.args:
        note = raw.strip().lstrip("#")
        if not note:
            continue
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", note):
            await update.effective_message.reply_text(f"❌ Netinkamas note vardas: {note}\nPvz.: /adsset ads konkursas promo")
            return
        if note not in notes:
            notes.append(note)

    if not notes:
        await update.effective_message.reply_text("Naudojimas:\n/adsset ads konkursas promo")
        return

    set_setting("ads_notes", json.dumps(notes, ensure_ascii=False))
    set_setting("ads_next_index", "0")
    await update.effective_message.reply_text("✅ ADS rotacija:\n" + " → ".join(notes) + "\n\nIntervalą keisk su /adsinterval 10")


async def adsinterval_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        return
    if not context.args:
        await update.effective_message.reply_text("Naudojimas: /adsinterval 10")
        return
    try:
        minutes = int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text("❌ Intervalas turi būti skaičius.")
        return
    if minutes < 5 or minutes > 1440:
        await update.effective_message.reply_text("❌ Galimas intervalas: 5–1440 min.")
        return
    set_setting("ads_interval_minutes", str(minutes))
    await update.effective_message.reply_text(f"✅ ADS intervalas: kas {minutes} min.")


async def adson_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        return
    if not get_ads_notes():
        await update.effective_message.reply_text("Pirma nustatyk notes, pvz. /adsset ads konkursas")
        return
    set_setting("ads_enabled", "1")
    set_setting("ads_force_send", "1")
    await update.effective_message.reply_text("🟢 ADS rotacija įjungta.")


async def adsoff_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        return
    set_setting("ads_enabled", "0")
    set_setting("ads_force_send", "0")
    await update.effective_message.reply_text("🔴 ADS rotacija išjungta.")


async def adsnext_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        return
    if get_setting("ads_enabled", "0") != "1":
        await update.effective_message.reply_text("ADS rotacija išjungta. Pirma /adson.")
        return
    set_setting("ads_force_send", "1")
    await update.effective_message.reply_text("⏭ Kitas ADS bus paleistas netrukus.")


# ---------------------------------------------------------------------------
# LIVE TOP admin commands
# ---------------------------------------------------------------------------

async def liveboard_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        return
    set_setting("live_leaderboard_enabled", "1")
    message_id = await refresh_live_leaderboard(context.application)
    await update.effective_message.reply_text(f"✅ LIVE TOP įjungtas / atnaujintas. ID: {message_id}")


async def liveboardnew_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        return
    set_setting("live_leaderboard_enabled", "1")
    message_id = await refresh_live_leaderboard(context.application, force_new=True)
    await update.effective_message.reply_text(f"✅ Sukurtas naujas LIVE TOP postas. ID: {message_id}")


async def liveboardoff_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        return
    set_setting("live_leaderboard_enabled", "0")
    await update.effective_message.reply_text("🔴 LIVE TOP auto update išjungtas.")


async def topad_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        return
    group_id = await ensure_group(context)
    message = await context.bot.send_message(chat_id=group_id, text=live_leaderboard_text(), reply_markup=live_leaderboard_markup(context.application))
    await update.effective_message.reply_text(f"✅ Papildomas TOP postas įkeltas. ID: {message.message_id}")


# ---------------------------------------------------------------------------
# Membership tracking
# ---------------------------------------------------------------------------

def became_member(old_status: str, new_status: str) -> bool:
    old_in = old_status in {ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER, ChatMemberStatus.RESTRICTED}
    new_in = new_status in {ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER, ChatMemberStatus.RESTRICTED}
    return (not old_in) and new_in


async def on_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    change = update.chat_member
    if not change:
        return
    group_id = await ensure_group(context)
    if change.chat.id != group_id:
        return

    joined_user = change.new_chat_member.user
    if not became_member(change.old_chat_member.status, change.new_chat_member.status):
        return
    if joined_user.is_bot:
        return

    inviter_id = None
    source = None

    if change.invite_link:
        inviter_id = owner_by_link(group_id, change.invite_link.invite_link)
        if inviter_id:
            source = "ref_link"

    if (
        inviter_id is None
        and not getattr(change, "via_join_request", False)
        and not getattr(change, "via_chat_folder_invite_link", False)
        and change.from_user
        and change.from_user.id != joined_user.id
        and not change.from_user.is_bot
    ):
        inviter_id = change.from_user.id
        source = "direct_add"
        upsert_user(change.from_user)

    if not inviter_id:
        return

    awarded = award_once(group_id, joined_user.id, inviter_id, source)
    if not awarded:
        return

    inviter = get_user(inviter_id)
    weekly = int(inviter["weekly_points"]) if inviter else 0
    total = int(inviter["points"]) if inviter else 0

    try:
        await context.bot.send_message(chat_id=inviter_id, text=f"✅ +1 taškas!\n⭐ Šią savaitę: {weekly}\n📊 Iš viso: {total}")
    except Exception:
        pass

    log.info("Užskaitytas +1: inviter=%s joined=%s source=%s", inviter_id, joined_user.id, source)

    try:
        await refresh_live_leaderboard(context.application)
    except Exception:
        log.exception("Nepavyko atnaujinti LIVE TOP po +1")


# ---------------------------------------------------------------------------
# Background tasks and command menu
# ---------------------------------------------------------------------------

async def weekly_reset_loop(application: Application):
    while True:
        try:
            changed, old_week, previous_top = ensure_current_week()
            if changed:
                log.info("Savaitės resetas: %s -> %s", old_week, current_week_key())
                if WEEK_RESET_ANNOUNCE:
                    group_id = application.bot_data.get("group_id")
                    if group_id:
                        lines = [f"🏁 {old_week} savaitės invite konkursas baigtas!"]
                        if previous_top:
                            lines.extend(["", "🏆 TOP 3:"])
                            for index, row in enumerate(previous_top[:3], 1):
                                lines.append(f"{index}. {display_name(row)} — {row['weekly_points']} tšk.")
                        lines.extend(["", "🔄 Nauja savaitė prasidėjo — savaitės taškai vėl nuo 0."])
                        try:
                            await application.bot.send_message(chat_id=group_id, text="\n".join(lines))
                        except Exception:
                            log.exception("Nepavyko paskelbti savaitės reseto.")
                try:
                    await refresh_live_leaderboard(application)
                except Exception:
                    log.exception("Nepavyko atnaujinti LIVE TOP po reseto.")
        except Exception:
            log.exception("Weekly reset loop klaida")
        await asyncio.sleep(60)


async def set_command_menus(application: Application):
    user_commands = [
        BotCommand("start", "Atidaryti boto meniu"),
        BotCommand("mylink", "Gauti mano invite nuorodą"),
        BotCommand("points", "Mano taškai"),
        BotCommand("top", "Savaitės TOP 10"),
        BotCommand("alltime", "Viso laiko TOP 10"),
        BotCommand("lastweek", "Praėjusios savaitės TOP"),
        BotCommand("help", "Vartotojo komandos"),
        BotCommand("how", "Kaip veikia konkursas"),
    ]
    await application.bot.set_my_commands(user_commands)

    group_id = application.bot_data.get("group_id")
    if not group_id:
        return

    admin_commands = user_commands + [
        BotCommand("admin", "Admin panelė"),
        BotCommand("stats", "Sistemos statistika"),
        BotCommand("ads", "Rose ADS statusas"),
        BotCommand("adsset", "Nustatyti ADS notes"),
        BotCommand("adsinterval", "Keisti ADS intervalą"),
        BotCommand("adson", "Įjungti ADS"),
        BotCommand("adsoff", "Išjungti ADS"),
        BotCommand("adsnext", "Kitas ADS dabar"),
        BotCommand("liveboard", "Atnaujinti LIVE TOP"),
        BotCommand("liveboardnew", "Naujas LIVE TOP postas"),
        BotCommand("liveboardoff", "Išjungti LIVE TOP"),
        BotCommand("topad", "Papildomas TOP postas"),
        BotCommand("contestad", "Rose konkursas note tekstas"),
        BotCommand("adminnote", "Visos admin komandos"),
    ]

    try:
        await application.bot.set_my_commands(admin_commands, scope=BotCommandScopeChatAdministrators(chat_id=group_id))
    except Exception:
        log.exception("Nepavyko nustatyti admin command menu")


async def post_init(application: Application):
    chat = await application.bot.get_chat(GROUP_CHAT_RAW)
    me = await application.bot.get_me()

    application.bot_data["group_id"] = chat.id
    application.bot_data["group_title"] = chat.title or str(chat.id)
    application.bot_data["bot_username"] = me.username
    application.bot_data["_leaderboard_lock"] = asyncio.Lock()

    ensure_current_week()
    await set_command_menus(application)

    application.bot_data["_weekly_reset_task"] = asyncio.create_task(weekly_reset_loop(application))

    if get_setting("live_leaderboard_enabled", "1" if LIVE_LEADERBOARD_ENABLED else "0") == "1":
        try:
            await refresh_live_leaderboard(application)
        except Exception:
            log.exception("Nepavyko paleisti LIVE TOP")
        application.bot_data["_live_leaderboard_task"] = asyncio.create_task(live_leaderboard_loop(application))

    log.info("Botas @%s prijungtas prie %s (%s)", me.username, chat.title, chat.id)


async def post_shutdown(application: Application):
    for key in ("_weekly_reset_task", "_live_leaderboard_task"):
        task = application.bot_data.get(key)
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


def main():
    init_db()

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("mylink", mylink_cmd))
    app.add_handler(CommandHandler("points", points_cmd))
    app.add_handler(CommandHandler("top", top_cmd))
    app.add_handler(CommandHandler("alltime", alltime_cmd))
    app.add_handler(CommandHandler("lastweek", lastweek_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("commands", help_cmd))
    app.add_handler(CommandHandler("how", how_cmd))
    app.add_handler(CommandHandler("kaipveikia", how_cmd))

    app.add_handler(CommandHandler("admin", admin_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("contestad", contestad_cmd))
    app.add_handler(CommandHandler("adminnote", adminnote_cmd))

    app.add_handler(CommandHandler("ads", ads_cmd))
    app.add_handler(CommandHandler("adsset", adsset_cmd))
    app.add_handler(CommandHandler("adsinterval", adsinterval_cmd))
    app.add_handler(CommandHandler("adson", adson_cmd))
    app.add_handler(CommandHandler("adsoff", adsoff_cmd))
    app.add_handler(CommandHandler("adsnext", adsnext_cmd))

    app.add_handler(CommandHandler("liveboard", liveboard_cmd))
    app.add_handler(CommandHandler("liveboardnew", liveboardnew_cmd))
    app.add_handler(CommandHandler("liveboardoff", liveboardoff_cmd))
    app.add_handler(CommandHandler("topad", topad_cmd))

    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(ChatMemberHandler(on_chat_member, ChatMemberHandler.CHAT_MEMBER))

    print("Referral / konkurso botas paleistas.")
    print(f"Savaitės resetas: pirmadienį 00:00 ({WEEK_TIMEZONE})")
    print("CTRL+C sustabdyti.")

    app.run_polling(
        allowed_updates=["message", "callback_query", "chat_member", "my_chat_member"],
        drop_pending_updates=False,
    )


if __name__ == "__main__":
    main()
