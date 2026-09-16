import asyncio
import html
import json
import logging
import os
import re
import sqlite3
from contextlib import closing, suppress
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from telegram import (
    BotCommand,
    BotCommandScopeChatAdministrators,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ChatMemberStatus, ChatType, ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

load_dotenv()

BOT_TOKEN = os.environ["BOT_TOKEN"].strip()
GROUP_CHAT_RAW = os.getenv("GROUP", os.getenv("GROUP_CHAT", "@NERADAUDROPO")).strip()
GROUP_PUBLIC_URL = os.getenv("GROUP_PUBLIC_URL", "").strip()
if not GROUP_PUBLIC_URL and GROUP_CHAT_RAW.startswith("@"):
    GROUP_PUBLIC_URL = "https://t.me/" + GROUP_CHAT_RAW[1:]

DB_PATH = os.getenv("DB_PATH", "referrals.sqlite3").strip()
WEEK_TIMEZONE = os.getenv("WEEK_TIMEZONE", "Europe/Vilnius").strip()
WEEK_RESET_ANNOUNCE = os.getenv("WEEK_RESET_ANNOUNCE", "true").lower() in {"1", "true", "yes", "on"}
LIVE_LEADERBOARD_ENABLED = os.getenv("LIVE_LEADERBOARD_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
LIVE_LEADERBOARD_REFRESH_SECONDS = max(15, int(os.getenv("LIVE_LEADERBOARD_REFRESH_SECONDS", "60")))
EMOJI_IDS_FILE = Path(os.getenv("EMOJI_IDS_FILE", "emoji_ids.json"))
ASSETS_DIR = Path(os.getenv("ASSETS_DIR", "assets"))
ADMIN_IDS = {
    int(item.strip())
    for item in os.getenv("ADMIN_IDS", "").split(",")
    if item.strip().isdigit()
}

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("nera_dropo_bot")

FALLBACK_EMOJI = {
    "brand": "👑",
    "crown": "👑",
    "invite": "🔗",
    "share": "📤",
    "trophy": "🏆",
    "group": "👥",
    "add": "➕",
    "stats": "📈",
}


def load_emoji_ids():
    if not EMOJI_IDS_FILE.exists():
        return {}
    try:
        data = json.loads(EMOJI_IDS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        log.exception("Nepavyko perskaityti emoji_ids.json")
        return {}


EMOJI_IDS = load_emoji_ids()


def em(key: str) -> str:
    fallback = FALLBACK_EMOJI.get(key, "✨")
    emoji_id = str(EMOJI_IDS.get(key, "")).strip()
    if emoji_id:
        return f'<tg-emoji emoji-id="{html.escape(emoji_id)}">{fallback}</tg-emoji>'
    return fallback


def brand_footer() -> str:
    return f"{em('brand')} <b>NĖRA DROPO</b>"


def esc(value) -> str:
    return html.escape(str(value or ""))


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
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS invite_links (
                group_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                invite_link TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                PRIMARY KEY (group_id, user_id)
            );

            CREATE TABLE IF NOT EXISTS referrals (
                group_id INTEGER NOT NULL,
                joined_user_id INTEGER NOT NULL,
                inviter_user_id INTEGER NOT NULL,
                source TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (group_id, joined_user_id)
            );

            CREATE TABLE IF NOT EXISTS message_stats (
                group_id INTEGER NOT NULL,
                week_key TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                username TEXT,
                first_name TEXT,
                message_count INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (group_id, week_key, user_id)
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

            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_ref_inviter ON referrals(inviter_user_id);
            CREATE INDEX IF NOT EXISTS idx_msg_week ON message_stats(group_id, week_key, message_count DESC);
            CREATE INDEX IF NOT EXISTS idx_weekly_history ON weekly_history(week_key, points DESC);
            """
        )
        defaults = {
            "ads_enabled": "0",
            "ads_interval_minutes": "30",
            "ads_fast_interval_minutes": "15",
            "ads_notes": json.dumps(["konkursas", "promo"], ensure_ascii=False),
            "ads_next_index": "0",
            "ads_fast_index": "0",
            "ads_normal_index": "0",
            "ads_force_send": "0",
            "live_leaderboard_enabled": "1",
        }
        for key, value in defaults.items():
            conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES(?, ?)", (key, value))
        conn.commit()
    ensure_current_week()


def get_setting(key: str, default=None):
    with closing(db()) as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key: str, value):
    with closing(db()) as conn:
        conn.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )
        conn.commit()


def ensure_current_week():
    week_key = current_week_key()
    with closing(db()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT value FROM meta WHERE key='current_week'").fetchone()
        if row is None:
            conn.execute("INSERT INTO meta(key, value) VALUES('current_week', ?)", (week_key,))
            conn.commit()
            return False, None, []
        old_week = row["value"]
        if old_week == week_key:
            conn.commit()
            return False, None, []
        previous_top = conn.execute(
            "SELECT user_id, username, first_name, weekly_points FROM users WHERE weekly_points > 0 ORDER BY weekly_points DESC, user_id ASC"
        ).fetchall()
        conn.execute(
            "INSERT OR REPLACE INTO weekly_history (week_key,user_id,username,first_name,points,saved_at) "
            "SELECT ?,user_id,username,first_name,weekly_points,? FROM users WHERE weekly_points>0",
            (old_week, now_iso()),
        )
        conn.execute("UPDATE users SET weekly_points=0")
        conn.execute("UPDATE meta SET value=? WHERE key='current_week'", (week_key,))
        conn.commit()
        return True, old_week, previous_top


def upsert_user(user):
    if not user:
        return
    ensure_current_week()
    with closing(db()) as conn:
        conn.execute(
            "INSERT INTO users(user_id,username,first_name,points,weekly_points,created_at) VALUES(?,?,?,0,0,?) "
            "ON CONFLICT(user_id) DO UPDATE SET username=excluded.username,first_name=excluded.first_name",
            (user.id, user.username, user.first_name, now_iso()),
        )
        conn.commit()


def get_user(user_id: int):
    with closing(db()) as conn:
        return conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()


def get_personal_link(group_id: int, user_id: int):
    with closing(db()) as conn:
        row = conn.execute("SELECT invite_link FROM invite_links WHERE group_id=? AND user_id=?", (group_id, user_id)).fetchone()
        return row["invite_link"] if row else None


def save_personal_link(group_id: int, user_id: int, link: str):
    with closing(db()) as conn:
        conn.execute(
            "INSERT INTO invite_links(group_id,user_id,invite_link,created_at) VALUES(?,?,?,?) "
            "ON CONFLICT(group_id,user_id) DO UPDATE SET invite_link=excluded.invite_link,created_at=excluded.created_at",
            (group_id, user_id, link, now_iso()),
        )
        conn.commit()


def owner_by_link(group_id: int, link: str):
    with closing(db()) as conn:
        row = conn.execute("SELECT user_id FROM invite_links WHERE group_id=? AND invite_link=?", (group_id, link)).fetchone()
        return int(row["user_id"]) if row else None


def award_once(group_id: int, joined_user_id: int, inviter_user_id: int, source: str):
    if joined_user_id == inviter_user_id:
        return False
    ensure_current_week()
    with closing(db()) as conn:
        try:
            conn.execute(
                "INSERT INTO referrals(group_id,joined_user_id,inviter_user_id,source,created_at) VALUES(?,?,?,?,?)",
                (group_id, joined_user_id, inviter_user_id, source, now_iso()),
            )
            conn.execute(
                "INSERT INTO users(user_id,username,first_name,points,weekly_points,created_at) VALUES(?,NULL,NULL,1,1,?) "
                "ON CONFLICT(user_id) DO UPDATE SET points=points+1,weekly_points=weekly_points+1",
                (inviter_user_id, now_iso()),
            )
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False


def record_group_message(group_id: int, user):
    if not user or user.is_bot:
        return
    ensure_current_week()
    with closing(db()) as conn:
        conn.execute(
            "INSERT INTO message_stats(group_id,week_key,user_id,username,first_name,message_count,updated_at) VALUES(?,?,?,?,?,1,?) "
            "ON CONFLICT(group_id,week_key,user_id) DO UPDATE SET username=excluded.username,first_name=excluded.first_name,message_count=message_count+1,updated_at=excluded.updated_at",
            (group_id, current_week_key(), user.id, user.username, user.first_name, now_iso()),
        )
        conn.commit()


async def refresh_group_admin_ids(application: Application):
    group_id = application.bot_data.get("group_id")
    excluded = set(ADMIN_IDS)
    if group_id:
        try:
            admins = await application.bot.get_chat_administrators(group_id)
            excluded.update(member.user.id for member in admins if member.user)
        except Exception:
            log.exception("Nepavyko gauti grupės adminų")
    application.bot_data["excluded_top_admin_ids"] = excluded
    return excluded


def filter_rows(rows, exclude_ids=None, limit=10):
    excluded = set(exclude_ids or set())
    out = []
    for row in rows:
        if int(row["user_id"]) in excluded:
            continue
        out.append(row)
        if len(out) >= limit:
            break
    return out


def weekly_top(limit=10, exclude_ids=None):
    with closing(db()) as conn:
        rows = conn.execute(
            "SELECT user_id,username,first_name,weekly_points FROM users WHERE weekly_points>0 ORDER BY weekly_points DESC,user_id ASC"
        ).fetchall()
    return filter_rows(rows, exclude_ids, limit)


def alltime_top(limit=10, exclude_ids=None):
    with closing(db()) as conn:
        rows = conn.execute("SELECT user_id,username,first_name,points FROM users WHERE points>0 ORDER BY points DESC,user_id ASC").fetchall()
    return filter_rows(rows, exclude_ids, limit)


def last_week_top(limit=10, exclude_ids=None):
    with closing(db()) as conn:
        wk = conn.execute("SELECT week_key FROM weekly_history ORDER BY saved_at DESC LIMIT 1").fetchone()
        if not wk:
            return None, []
        rows = conn.execute(
            "SELECT user_id,username,first_name,points FROM weekly_history WHERE week_key=? ORDER BY points DESC,user_id ASC",
            (wk["week_key"],),
        ).fetchall()
    return wk["week_key"], filter_rows(rows, exclude_ids, limit)


def weekly_message_top(group_id: int, limit=10, exclude_ids=None):
    with closing(db()) as conn:
        rows = conn.execute(
            "SELECT user_id,username,first_name,message_count FROM message_stats WHERE group_id=? AND week_key=? AND message_count>0 ORDER BY message_count DESC,user_id ASC",
            (group_id, current_week_key()),
        ).fetchall()
    return filter_rows(rows, exclude_ids, limit)


def display_name(row):
    if row["username"]:
        return f"@{esc(row['username'])}"
    if row["first_name"]:
        return esc(row["first_name"])
    return f"ID {row['user_id']}"


def ranking_lines(rows, value_key, suffix):
    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for i, row in enumerate(rows, 1):
        prefix = medals[i - 1] if i <= 3 else f"{i}."
        lines.append(f"{prefix} {display_name(row)} — <b>{row[value_key]}</b> {suffix}")
    return lines


def weekly_top_text(excluded=None):
    rows = weekly_top(exclude_ids=excluded)
    lines = [f"{em('trophy')} <b>SAVAITĖS INVITE TOP 10</b>", ""]
    lines += ranking_lines(rows, "weekly_points", "tšk.") if rows else ["Kol kas TOP tuščias 👀"]
    lines += ["", "🔄 Reset: pirmadienį 00:00", brand_footer()]
    return "\n".join(lines)


def message_top_text(group_id: int, excluded=None):
    rows = weekly_message_top(group_id, exclude_ids=excluded)
    lines = ["💬 <b>SAVAITĖS ŽINUČIŲ TOP 10</b>", ""]
    lines += ranking_lines(rows, "message_count", "žin.") if rows else ["Kol kas TOP tuščias 👀"]
    lines += ["", "🔄 Reset: pirmadienį 00:00", brand_footer()]
    return "\n".join(lines)


def alltime_top_text(excluded=None):
    rows = alltime_top(exclude_ids=excluded)
    lines = [f"{em('stats')} <b>VISO LAIKO INVITE TOP 10</b>", ""]
    lines += ranking_lines(rows, "points", "tšk.") if rows else ["Kol kas TOP tuščias 👀"]
    lines += ["", brand_footer()]
    return "\n".join(lines)


def lastweek_top_text(excluded=None):
    wk, rows = last_week_top(exclude_ids=excluded)
    if not rows:
        return f"🥇 <b>PRAEITA SAVAITĖ</b>\n\nRezultatų dar nėra.\n\n{brand_footer()}"
    lines = [f"🥇 <b>PRAEITA SAVAITĖ · {esc(wk)}</b>", ""]
    lines += ranking_lines(rows, "points", "tšk.")
    lines += ["", brand_footer()]
    return "\n".join(lines)


def live_top_text(excluded=None):
    rows = weekly_top(exclude_ids=excluded)
    lines = [f"{em('trophy')} <b>SAVAITĖS INVITE TOP 10 · LIVE</b>", ""]
    lines += ranking_lines(rows, "weekly_points", "tšk.") if rows else ["Būk pirmas 👀"]
    lines += ["", "🔄 Pirmadienį 00:00", f"🟢 {local_now():%H:%M}", brand_footer()]
    return "\n".join(lines)


async def ensure_group(context: ContextTypes.DEFAULT_TYPE):
    if "group_id" not in context.application.bot_data:
        chat = await context.bot.get_chat(GROUP_CHAT_RAW)
        context.application.bot_data["group_id"] = chat.id
        context.application.bot_data["group_title"] = chat.title or str(chat.id)
    return context.application.bot_data["group_id"]


async def is_admin_user(user_id: int, context: ContextTypes.DEFAULT_TYPE):
    if user_id in ADMIN_IDS:
        return True
    try:
        group_id = await ensure_group(context)
        member = await context.bot.get_chat_member(group_id, user_id)
        return member.status in {ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER}
    except Exception:
        return False


async def require_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ok = bool(user and await is_admin_user(user.id, context))
    if not ok and update.effective_message:
        await update.effective_message.reply_text("⛔ Tik grupės adminui.")
    return ok


def bot_url(application, start_param=None):
    username = application.bot_data.get("bot_username")
    if not username:
        return None
    base = f"https://t.me/{username}"
    return f"{base}?start={start_param}" if start_param else base


def share_url(invite_link: str):
    text = "Prisijunk prie NĖRA DROPO 👑"
    return f"https://t.me/share/url?url={quote(invite_link, safe='')}&text={quote(text, safe='')}"


def user_menu(is_admin=False):
    rows = [
        [InlineKeyboardButton("🔗 MANO INVITE", callback_data="my_link"), InlineKeyboardButton("⭐ MANO TAŠKAI", callback_data="points")],
        [InlineKeyboardButton("🏆 INVITE TOP", callback_data="top"), InlineKeyboardButton("💬 ŽINUČIŲ TOP", callback_data="msgtop")],
        [InlineKeyboardButton("📈 VISO TOP", callback_data="alltime"), InlineKeyboardButton("🥇 PRAEITA SAVAITĖ", callback_data="lastweek")],
        [InlineKeyboardButton("ℹ️ KAIP VEIKIA", callback_data="info")],
    ]
    if is_admin:
        rows.append([InlineKeyboardButton("🛠 ADMIN PANEL", callback_data="admin_panel")])
    return InlineKeyboardMarkup(rows)


def admin_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📣 ADS STATUS", callback_data="admin_ads_status"), InlineKeyboardButton("⏭ ADS DABAR", callback_data="admin_ads_next")],
        [InlineKeyboardButton("🟢 ADS ON", callback_data="admin_ads_on"), InlineKeyboardButton("🔴 ADS OFF", callback_data="admin_ads_off")],
        [InlineKeyboardButton("🏆 REFRESH TOP", callback_data="admin_live_refresh"), InlineKeyboardButton("📊 STATISTIKA", callback_data="admin_stats")],
        [InlineKeyboardButton("🖼 ADS PREVIEW", callback_data="admin_adpack"), InlineKeyboardButton("📋 KOMANDOS", callback_data="admin_commands")],
        [InlineKeyboardButton("⬅️ ATGAL", callback_data="back")],
    ])


def live_markup(application):
    url = bot_url(application, "invite")
    rows = []
    if url:
        rows.append([InlineKeyboardButton("🎯 DALYVAUTI / GAUTI INVITE", url=url)])
    if GROUP_PUBLIC_URL:
        rows.append([InlineKeyboardButton("👑 NĖRA DROPO", url=GROUP_PUBLIC_URL)])
    return InlineKeyboardMarkup(rows) if rows else None


async def refresh_live_leaderboard(application: Application, force_new=False):
    if get_setting("live_leaderboard_enabled", "1") != "1":
        return None
    group_id = application.bot_data.get("group_id")
    if not group_id:
        return None
    lock = application.bot_data.setdefault("_leaderboard_lock", asyncio.Lock())
    async with lock:
        excluded = await refresh_group_admin_ids(application)
        key = f"live_leaderboard_message_id:{group_id}"
        raw = get_setting(key, "")
        try:
            message_id = int(raw) if raw else None
        except ValueError:
            message_id = None
        text = live_top_text(excluded)
        markup = live_markup(application)
        if message_id and not force_new:
            try:
                await application.bot.edit_message_text(
                    chat_id=group_id, message_id=message_id, text=text, parse_mode=ParseMode.HTML, reply_markup=markup
                )
                return message_id
            except Exception as exc:
                if "not modified" in str(exc).lower():
                    return message_id
                log.warning("LIVE TOP edit nepavyko: %s", exc)
        msg = await application.bot.send_message(chat_id=group_id, text=text, parse_mode=ParseMode.HTML, reply_markup=markup)
        set_setting(key, str(msg.message_id))
        return msg.message_id


async def ensure_personal_link(context: ContextTypes.DEFAULT_TYPE, user):
    upsert_user(user)
    group_id = await ensure_group(context)
    existing = get_personal_link(group_id, user.id)
    if existing:
        return existing
    obj = await context.bot.create_chat_invite_link(chat_id=group_id, name=f"ref_{user.id}"[:32])
    save_personal_link(group_id, user.id, obj.invite_link)
    return obj.invite_link


def invite_text(link: str):
    return (
        f"{em('invite')} <b>TAVO INVITE</b>\n\n"
        f"{esc(link)}\n\n"
        f"{em('trophy')} <b>+1 taškas</b> kai:\n"
        f"• žmogus ateina per tavo nuorodą\n"
        f"• pats žmogų <b>pridedi į grupę</b>\n\n"
        f"Tas pats žmogus = 1 kartas.\n\n"
        f"{brand_footer()}"
    )


def invite_markup(link: str):
    rows = [[InlineKeyboardButton("📤 DALINTIS SU DRAUGAIS / GRUPĖMIS", url=share_url(link))]]
    if GROUP_PUBLIC_URL:
        rows.append([InlineKeyboardButton("👑 ATIDARYTI NĖRA DROPO", url=GROUP_PUBLIC_URL)])
    return InlineKeyboardMarkup(rows)


def contest_rose_caption(application):
    b = bot_url(application, "invite") or "https://t.me/TAVO_BOTO_USERNAME"
    g = GROUP_PUBLIC_URL or "https://t.me/NERADAUDROPO"
    return "\n".join([
        "🏆 SAVAITĖS INVITE KONKURSAS",
        "",
        "🔗 Gauk savo invite ir dalinkis.",
        "👥 Naujas narys = +1 taškas.",
        "➕ Pridedi žmogų į grupę = irgi +1.",
        "",
        "🔄 Reset: pirmadienį 00:00",
        "🛡 Adminai TOP'e nerodomi.",
        "",
        f"[🎯 DALYVAUTI](buttonurl://{b})",
        f"[👑 NĖRA DROPO](buttonurl://{g}:same)",
    ])


def promo_rose_caption(application):
    b = bot_url(application, "invite") or "https://t.me/TAVO_BOTO_USERNAME"
    g = GROUP_PUBLIC_URL or "https://t.me/NERADAUDROPO"
    return "\n".join([
        "👑 NĖRA DROPO",
        "",
        "Visa info vienoje vietoje 👀",
        "🏆 Savaitinis invite konkursas",
        "💬 Aktyviausių narių TOP",
        "📈 Bendruomenė auga kasdien",
        "",
        f"[🚀 PRISIJUNGTI](buttonurl://{g})",
        f"[🎯 DALYVAUTI](buttonurl://{b}:same)",
    ])


def native_ad_markup(application):
    b = bot_url(application, "invite")
    rows = []
    if b:
        rows.append([InlineKeyboardButton("🎯 DALYVAUTI / GAUTI INVITE", url=b)])
    if GROUP_PUBLIC_URL:
        rows.append([InlineKeyboardButton("👑 NĖRA DROPO", url=GROUP_PUBLIC_URL)])
    return InlineKeyboardMarkup(rows) if rows else None


async def send_ad_preview(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    contest = ASSETS_DIR / "ads" / "contest.png"
    promo = ASSETS_DIR / "ads" / "promo.png"
    contest_text = (
        f"{em('trophy')} <b>SAVAITĖS INVITE KONKURSAS</b>\n\n"
        f"{em('invite')} Gauk invite ir dalinkis.\n"
        f"{em('group')} Naujas narys = <b>+1</b>.\n"
        f"{em('add')} Add Member = <b>+1</b>.\n\n"
        f"🔄 Pirmadienį 00:00\n\n{brand_footer()}"
    )
    promo_text = (
        f"{em('brand')} <b>NĖRA DROPO</b>\n\n"
        f"Visa info vienoje vietoje 👀\n"
        f"{em('trophy')} Invite konkursas\n"
        f"💬 Aktyviausių TOP\n"
        f"{em('stats')} Bendruomenė auga\n\n{brand_footer()}"
    )
    if contest.exists():
        await context.bot.send_photo(chat_id=chat_id, photo=contest.read_bytes(), caption=contest_text, parse_mode=ParseMode.HTML, reply_markup=native_ad_markup(context.application))
    else:
        await context.bot.send_message(chat_id=chat_id, text=contest_text, parse_mode=ParseMode.HTML, reply_markup=native_ad_markup(context.application))
    if promo.exists():
        await context.bot.send_photo(chat_id=chat_id, photo=promo.read_bytes(), caption=promo_text, parse_mode=ParseMode.HTML, reply_markup=native_ad_markup(context.application))
    else:
        await context.bot.send_message(chat_id=chat_id, text=promo_text, parse_mode=ParseMode.HTML, reply_markup=native_ad_markup(context.application))


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return
    upsert_user(user)
    if update.effective_chat.type != ChatType.PRIVATE:
        url = bot_url(context.application, "invite")
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("🎯 ATIDARYTI BOTĄ", url=url)]]) if url else None
        await update.effective_message.reply_text("🎯 Konkursą valdai privačiame bote.", reply_markup=markup)
        return
    is_admin = await is_admin_user(user.id, context)
    if context.args and context.args[0].lower() == "invite":
        link = await ensure_personal_link(context, user)
        await update.effective_message.reply_text(invite_text(link), parse_mode=ParseMode.HTML, reply_markup=invite_markup(link), disable_web_page_preview=True)
        return
    title = esc(context.application.bot_data.get("group_title", "NĖRA DROPO"))
    await update.effective_message.reply_text(
        f"{em('brand')} <b>{title}</b>\n\nPasirink veiksmą 👇", parse_mode=ParseMode.HTML, reply_markup=user_menu(is_admin)
    )


async def mylink_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if update.effective_chat.type != ChatType.PRIVATE:
        url = bot_url(context.application, "invite")
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔗 GAUTI MANO INVITE", url=url)]]) if url else None
        await update.effective_message.reply_text("🔗 Invite gausi privačiame bote.", reply_markup=markup)
        return
    link = await ensure_personal_link(context, user)
    await update.effective_message.reply_text(invite_text(link), parse_mode=ParseMode.HTML, reply_markup=invite_markup(link), disable_web_page_preview=True)


async def points_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    upsert_user(update.effective_user)
    row = get_user(update.effective_user.id)
    weekly = int(row["weekly_points"]) if row else 0
    total = int(row["points"]) if row else 0
    await update.effective_message.reply_text(
        f"⭐ <b>Šią savaitę:</b> {weekly}\n📈 <b>Iš viso:</b> {total}\n\n{brand_footer()}", parse_mode=ParseMode.HTML
    )


async def top_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    excluded = await refresh_group_admin_ids(context.application)
    await update.effective_message.reply_text(weekly_top_text(excluded), parse_mode=ParseMode.HTML)


async def msgtop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    group_id = await ensure_group(context)
    excluded = await refresh_group_admin_ids(context.application)
    await update.effective_message.reply_text(message_top_text(group_id, excluded), parse_mode=ParseMode.HTML)


async def alltime_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    excluded = await refresh_group_admin_ids(context.application)
    await update.effective_message.reply_text(alltime_top_text(excluded), parse_mode=ParseMode.HTML)


async def lastweek_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    excluded = await refresh_group_admin_ids(context.application)
    await update.effective_message.reply_text(lastweek_top_text(excluded), parse_mode=ParseMode.HTML)


async def how_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        f"ℹ️ <b>KAIP VEIKIA</b>\n\n"
        f"{em('invite')} Pasiimi savo invite.\n"
        f"{em('share')} Daliniesi draugams / grupėms.\n"
        f"{em('group')} Naujas narys = <b>+1</b>.\n"
        f"{em('add')} Pats pridedi žmogų = <b>+1</b>.\n"
        f"{em('trophy')} TOP reset: pirmadienį 00:00.\n"
        f"🛡 Adminai TOP'e nerodomi.\n\n{brand_footer()}"
    )
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)


async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        return
    await update.effective_message.reply_text("🛠 <b>ADMIN PANEL</b>", parse_mode=ParseMode.HTML, reply_markup=admin_menu())


def ads_status_text():
    try:
        notes = json.loads(get_setting("ads_notes", "[]"))
    except Exception:
        notes = []
    return (
        "📣 <b>AUTO ADS</b>\n\n"
        f"Būsena: {'🟢 ON' if get_setting('ads_enabled','0') == '1' else '🔴 OFF'}\n"
        f"FAST: kas {esc(get_setting('ads_fast_interval_minutes','15'))} min.\n"
        f"NORMAL: kas {esc(get_setting('ads_interval_minutes','30'))} min.\n"
        f"Notes: {esc(', '.join(notes) if notes else '—')}\n"
        f"Paskutinis: {esc(get_setting('ads_last_note','—') or '—')}\n"
        f"Rezultatas: {esc(get_setting('ads_last_result','—') or '—')}"
    )


async def ads_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        return
    await update.effective_message.reply_text(ads_status_text(), parse_mode=ParseMode.HTML)


async def adsset_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        return
    notes = []
    for raw in context.args:
        note = raw.strip().lstrip("#")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", note):
            await update.effective_message.reply_text("❌ Pvz.: /adsset konkursas promo partneris")
            return
        if note not in notes:
            notes.append(note)
    if not notes:
        await update.effective_message.reply_text("Naudojimas: /adsset konkursas promo")
        return
    set_setting("ads_notes", json.dumps(notes, ensure_ascii=False))
    set_setting("ads_next_index", "0")
    set_setting("ads_fast_index", "0")
    set_setting("ads_normal_index", "0")
    set_setting("ads_scheduler_initialized", "0")
    await update.effective_message.reply_text("✅ ADS eilė: " + " → ".join(notes))


async def adsinterval_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        return
    try:
        minutes = int(context.args[0])
    except Exception:
        await update.effective_message.reply_text("Pvz.: /adsinterval 30")
        return
    if not 5 <= minutes <= 1440:
        await update.effective_message.reply_text("❌ 5–1440 min.")
        return
    set_setting("ads_interval_minutes", str(minutes))
    await update.effective_message.reply_text(f"✅ NORMAL ADS: kas {minutes} min.")


async def adsfast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        return
    try:
        minutes = int(context.args[0])
    except Exception:
        await update.effective_message.reply_text("Pvz.: /adsfast 15\nTestui: /adsfast 5")
        return
    if not 5 <= minutes <= 1440:
        await update.effective_message.reply_text("❌ 5–1440 min.")
        return
    set_setting("ads_fast_interval_minutes", str(minutes))
    set_setting("ads_scheduler_initialized", "0")
    await update.effective_message.reply_text(f"✅ FAST ADS: kas {minutes} min.")


async def adson_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        return
    set_setting("ads_enabled", "1")
    set_setting("ads_scheduler_initialized", "0")
    await update.effective_message.reply_text("🟢 AUTO ADS įjungtas.")


async def adsoff_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        return
    set_setting("ads_enabled", "0")
    set_setting("ads_force_send", "0")
    await update.effective_message.reply_text("🔴 AUTO ADS išjungtas.")


async def adsnext_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        return
    set_setting("ads_force_send", "1")
    await update.effective_message.reply_text("⏭ Kitas ADS bus paleistas per kelias sekundes.")


async def contestad_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        return
    await update.effective_message.reply_text(contest_rose_caption(context.application), disable_web_page_preview=True)


async def promoad_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        return
    await update.effective_message.reply_text(promo_rose_caption(context.application), disable_web_page_preview=True)


async def adpack_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        return
    await send_ad_preview(update.effective_chat.id, context)


async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        return
    group_id = await ensure_group(context)
    with closing(db()) as conn:
        users = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
        refs = conn.execute("SELECT COUNT(*) c FROM referrals").fetchone()["c"]
        msgs = conn.execute(
            "SELECT COALESCE(SUM(message_count),0) c FROM message_stats WHERE group_id=? AND week_key=?",
            (group_id, current_week_key()),
        ).fetchone()["c"]
    await update.effective_message.reply_text(
        f"📊 <b>STATISTIKA</b>\n\n👥 Users: {users}\n🔗 Referrals: {refs}\n💬 Savaitės žinutės: {msgs}", parse_mode=ParseMode.HTML
    )


async def liveboard_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        return
    set_setting("live_leaderboard_enabled", "1")
    mid = await refresh_live_leaderboard(context.application)
    await update.effective_message.reply_text(f"✅ LIVE TOP atnaujintas. ID: {mid}")


async def liveboardnew_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        return
    set_setting("live_leaderboard_enabled", "1")
    mid = await refresh_live_leaderboard(context.application, force_new=True)
    await update.effective_message.reply_text(f"✅ Naujas LIVE TOP. ID: {mid}")


async def liveboardoff_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        return
    set_setting("live_leaderboard_enabled", "0")
    await update.effective_message.reply_text("🔴 LIVE TOP išjungtas.")


async def adminnote_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        return
    text = (
        "🛠 ADMIN\n\n"
        "/adpack — ADS preview su image\n"
        "/adsset konkursas promo — rotacija\n"
        "/adsfast 15 — konkursas/promo intervalas\n"
        "/adsinterval 30 — kitų ADS intervalas\n"
        "/adson · /adsoff · /adsnext · /ads\n"
        "/liveboard · /liveboardnew · /liveboardoff\n"
        "/stats · /contestad · /promoad"
    )
    await update.effective_message.reply_text(text)


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    user = q.from_user
    upsert_user(user)
    is_admin = await is_admin_user(user.id, context)
    if q.data == "back":
        await q.edit_message_text(f"{em('brand')} <b>NĖRA DROPO</b>\n\nPasirink veiksmą 👇", parse_mode=ParseMode.HTML, reply_markup=user_menu(is_admin))
        return
    if q.data == "my_link":
        link = await ensure_personal_link(context, user)
        await q.edit_message_text(invite_text(link), parse_mode=ParseMode.HTML, reply_markup=invite_markup(link), disable_web_page_preview=True)
        return
    if q.data == "points":
        row = get_user(user.id)
        await q.edit_message_text(
            f"⭐ <b>Šią savaitę:</b> {row['weekly_points'] if row else 0}\n📈 <b>Iš viso:</b> {row['points'] if row else 0}\n\n{brand_footer()}",
            parse_mode=ParseMode.HTML,
            reply_markup=user_menu(is_admin),
        )
        return
    excluded = await refresh_group_admin_ids(context.application)
    group_id = await ensure_group(context)
    if q.data == "top":
        await q.edit_message_text(weekly_top_text(excluded), parse_mode=ParseMode.HTML, reply_markup=user_menu(is_admin))
    elif q.data == "msgtop":
        await q.edit_message_text(message_top_text(group_id, excluded), parse_mode=ParseMode.HTML, reply_markup=user_menu(is_admin))
    elif q.data == "alltime":
        await q.edit_message_text(alltime_top_text(excluded), parse_mode=ParseMode.HTML, reply_markup=user_menu(is_admin))
    elif q.data == "lastweek":
        await q.edit_message_text(lastweek_top_text(excluded), parse_mode=ParseMode.HTML, reply_markup=user_menu(is_admin))
    elif q.data == "info":
        text = f"ℹ️ <b>KAIP VEIKIA</b>\n\n🔗 Invite → +1\n➕ Add Member → +1\n🏆 Reset pirmadienį 00:00\n🛡 Adminai TOP'e nerodomi.\n\n{brand_footer()}"
        await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=user_menu(is_admin))
    elif q.data == "admin_panel" and is_admin:
        await q.edit_message_text("🛠 <b>ADMIN PANEL</b>", parse_mode=ParseMode.HTML, reply_markup=admin_menu())
    elif q.data.startswith("admin_") and is_admin:
        if q.data == "admin_ads_status":
            text = ads_status_text()
        elif q.data == "admin_ads_next":
            set_setting("ads_force_send", "1")
            text = "⏭ ADS paleidžiamas."
        elif q.data == "admin_ads_on":
            set_setting("ads_enabled", "1")
            set_setting("ads_scheduler_initialized", "0")
            text = "🟢 AUTO ADS ON"
        elif q.data == "admin_ads_off":
            set_setting("ads_enabled", "0")
            text = "🔴 AUTO ADS OFF"
        elif q.data == "admin_live_refresh":
            mid = await refresh_live_leaderboard(context.application)
            text = f"✅ TOP atnaujintas · {mid}"
        elif q.data == "admin_stats":
            with closing(db()) as conn:
                users = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
                refs = conn.execute("SELECT COUNT(*) c FROM referrals").fetchone()["c"]
            text = f"📊 Users: {users}\n🔗 Referrals: {refs}"
        elif q.data == "admin_adpack":
            await send_ad_preview(user.id, context)
            text = "🖼 ADS preview išsiųsti žemiau."
        else:
            text = "📋 /adminnote"
        await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=admin_menu())


def became_member(old_status, new_status):
    active = {ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER, ChatMemberStatus.RESTRICTED}
    return old_status not in active and new_status in active


async def on_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    change = update.chat_member
    if not change:
        return
    group_id = await ensure_group(context)
    if change.chat.id != group_id:
        return
    joined = change.new_chat_member.user
    if joined.is_bot or not became_member(change.old_chat_member.status, change.new_chat_member.status):
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
        and change.from_user.id != joined.id
        and not change.from_user.is_bot
    ):
        inviter_id = change.from_user.id
        source = "direct_add"
        upsert_user(change.from_user)
    if not inviter_id or not award_once(group_id, joined.id, inviter_id, source):
        return
    inviter = get_user(inviter_id)
    try:
        await context.bot.send_message(
            chat_id=inviter_id,
            text=f"✅ <b>+1 taškas!</b>\n⭐ Savaitė: {inviter['weekly_points']}\n📈 Iš viso: {inviter['points']}",
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        pass
    await refresh_live_leaderboard(context.application)


async def on_group_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    user = update.effective_user
    if not message or not user or user.is_bot:
        return
    group_id = await ensure_group(context)
    if update.effective_chat.id != group_id:
        return
    if message.text and message.text.startswith("/"):
        return
    record_group_message(group_id, user)


async def weekly_reset_loop(application: Application):
    while True:
        try:
            changed, old_week, previous_top = ensure_current_week()
            if changed:
                excluded = await refresh_group_admin_ids(application)
                filtered = filter_rows(previous_top, excluded, 3)
                if WEEK_RESET_ANNOUNCE:
                    group_id = application.bot_data.get("group_id")
                    lines = ["🏁 <b>SAVAITĖ BAIGTA</b>", ""]
                    if filtered:
                        lines += ranking_lines(filtered, "weekly_points", "tšk.")
                    lines += ["", "🔄 Nauja savaitė prasidėjo.", brand_footer()]
                    await application.bot.send_message(chat_id=group_id, text="\n".join(lines), parse_mode=ParseMode.HTML)
                await refresh_live_leaderboard(application)
        except Exception:
            log.exception("Weekly reset klaida")
        await asyncio.sleep(60)


async def live_loop(application: Application):
    while True:
        await asyncio.sleep(LIVE_LEADERBOARD_REFRESH_SECONDS)
        try:
            await refresh_live_leaderboard(application)
        except Exception:
            log.exception("LIVE TOP update klaida")


async def set_command_menus(application: Application):
    user_commands = [
        BotCommand("start", "Atidaryti meniu"),
        BotCommand("mylink", "Mano invite + Dalintis"),
        BotCommand("points", "Mano taškai"),
        BotCommand("top", "Savaitės invite TOP"),
        BotCommand("msgtop", "Savaitės žinučių TOP"),
        BotCommand("alltime", "Viso laiko TOP"),
        BotCommand("lastweek", "Praeita savaitė"),
        BotCommand("how", "Kaip veikia"),
    ]
    await application.bot.set_my_commands(user_commands)
    group_id = application.bot_data.get("group_id")
    if group_id:
        admin_commands = user_commands + [
            BotCommand("admin", "Admin panel"),
            BotCommand("ads", "ADS status"),
            BotCommand("adsset", "Nustatyti ADS notes"),
            BotCommand("adsfast", "FAST intervalas"),
            BotCommand("adsinterval", "NORMAL intervalas"),
            BotCommand("adson", "ADS ON"),
            BotCommand("adsoff", "ADS OFF"),
            BotCommand("adsnext", "ADS dabar"),
            BotCommand("adpack", "ADS preview su image"),
            BotCommand("stats", "Statistika"),
        ]
        with suppress(Exception):
            await application.bot.set_my_commands(admin_commands, scope=BotCommandScopeChatAdministrators(chat_id=group_id))


async def post_init(application: Application):
    chat = await application.bot.get_chat(GROUP_CHAT_RAW)
    me = await application.bot.get_me()
    application.bot_data["group_id"] = chat.id
    application.bot_data["group_title"] = chat.title or "NĖRA DROPO"
    application.bot_data["bot_username"] = me.username
    set_setting("active_group_id", str(chat.id))
    await refresh_group_admin_ids(application)
    await set_command_menus(application)
    application.bot_data["_weekly_task"] = asyncio.create_task(weekly_reset_loop(application))
    if LIVE_LEADERBOARD_ENABLED and get_setting("live_leaderboard_enabled", "1") == "1":
        await refresh_live_leaderboard(application)
        application.bot_data["_live_task"] = asyncio.create_task(live_loop(application))
    log.info("Botas @%s → %s (%s)", me.username, chat.title, chat.id)


async def post_shutdown(application: Application):
    for key in ("_weekly_task", "_live_task"):
        task = application.bot_data.get(key)
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).post_shutdown(post_shutdown).build()
    for name, handler in [
        ("start", start_cmd), ("mylink", mylink_cmd), ("points", points_cmd),
        ("top", top_cmd), ("msgtop", msgtop_cmd), ("alltime", alltime_cmd),
        ("lastweek", lastweek_cmd), ("how", how_cmd), ("kaipveikia", how_cmd),
        ("admin", admin_cmd), ("stats", stats_cmd), ("ads", ads_cmd),
        ("adsset", adsset_cmd), ("adsinterval", adsinterval_cmd), ("adsfast", adsfast_cmd),
        ("adson", adson_cmd), ("adsoff", adsoff_cmd), ("adsnext", adsnext_cmd),
        ("contestad", contestad_cmd), ("promoad", promoad_cmd), ("adpack", adpack_cmd),
        ("liveboard", liveboard_cmd), ("liveboardnew", liveboardnew_cmd),
        ("liveboardoff", liveboardoff_cmd), ("adminnote", adminnote_cmd),
    ]:
        app.add_handler(CommandHandler(name, handler))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(ChatMemberHandler(on_chat_member, ChatMemberHandler.CHAT_MEMBER))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, on_group_message))
    print("NĖRA DROPO sistema paleista.")
    print(f"Grupė: {GROUP_CHAT_RAW}")
    print(f"Savaitės reset: pirmadienį 00:00 ({WEEK_TIMEZONE})")
    app.run_polling(
        allowed_updates=["message", "callback_query", "chat_member", "my_chat_member"],
        drop_pending_updates=False,
    )


if __name__ == "__main__":
    main()
