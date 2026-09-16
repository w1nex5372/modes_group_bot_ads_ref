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
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
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
GROUP_CHAT_RAW = os.getenv("GROUP", os.getenv("GROUP_CHAT", "@NERADAUDROPO")).strip()
GROUP_PUBLIC_URL = os.getenv("GROUP_PUBLIC_URL", "").strip()
if not GROUP_PUBLIC_URL and GROUP_CHAT_RAW.startswith("@"):
    GROUP_PUBLIC_URL = "https://t.me/" + GROUP_CHAT_RAW[1:]
DB_PATH = os.getenv("DB_PATH", "referrals.sqlite3").strip()

# Savaitė resetinama pagal šią laiko zoną.
WEEK_TIMEZONE = os.getenv("WEEK_TIMEZONE", "Europe/Vilnius").strip()
WEEK_RESET_ANNOUNCE = os.getenv("WEEK_RESET_ANNOUNCE", "true").lower() in {
    "1", "true", "yes", "on"
}

LIVE_LEADERBOARD_ENABLED = os.getenv("LIVE_LEADERBOARD_ENABLED", "true").lower() in {
    "1", "true", "yes", "on"
}
LIVE_LEADERBOARD_REFRESH_SECONDS = max(15, int(os.getenv("LIVE_LEADERBOARD_REFRESH_SECONDS", "60")))

ADMIN_IDS = {
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
}

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("referral_bot")


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

        # Migracija iš pirmos versijos: jei weekly_points dar nėra.
        cols = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(users)").fetchall()
        }
        if "weekly_points" not in cols:
            conn.execute(
                "ALTER TABLE users ADD COLUMN weekly_points INTEGER NOT NULL DEFAULT 0"
            )

        defaults = {
            "ads_enabled": "0",
            "ads_interval_minutes": "30",
            "ads_notes": json.dumps(["ads"], ensure_ascii=False),
            "ads_next_index": "0",
            "ads_last_sent_ts": "0",
            "ads_force_send": "0",
            "live_leaderboard_message_id": "",
            "live_leaderboard_enabled": "1",
        }
        for key, value in defaults.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings(key, value) VALUES(?, ?)",
                (key, value),
            )

        conn.commit()

    ensure_current_week()


def ensure_current_week():
    """
    Grąžina:
      (reset_happened, previous_week_key, previous_top_rows)

    Jei savaitė pasikeitė, išsaugo senos savaitės rezultatą,
    nunulina weekly_points ir pradeda naują savaitę.
    """
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


def save_invite_link(user_id: int, link: str):
    with closing(db()) as conn:
        conn.execute(
            "UPDATE users SET invite_link=? WHERE user_id=?",
            (link, user_id),
        )
        conn.commit()


def owner_by_link(link: str):
    with closing(db()) as conn:
        row = conn.execute(
            "SELECT user_id FROM users WHERE invite_link=?",
            (link,),
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
            # Tas pats žmogus šioje grupėje jau buvo užskaitytas anksčiau.
            return False


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

        wk = row["week_key"]
        rows = conn.execute(
            """
            SELECT user_id, username, first_name, points
            FROM weekly_history
            WHERE week_key=?
            ORDER BY points DESC, user_id ASC
            LIMIT ?
            """,
            (wk, limit),
        ).fetchall()
        return wk, rows


def display_name(row):
    if row["username"]:
        return f"@{row['username']}"
    if row["first_name"]:
        return row["first_name"]
    return f"ID {row['user_id']}"



def live_leaderboard_text():
    rows = weekly_top(10)
    now = local_now()
    lines = [
        "🏆 SAVAITĖS INVITE TOP 10 — LIVE",
        "",
        "👤 Kiekvienas naujas narys per tavo asmeninę invite nuorodą = +1 taškas.",
        "",
    ]
    if not rows:
        lines.append("Kol kas niekas taškų nesurinko. Būk pirmas 👀")
    else:
        medals = ["🥇", "🥈", "🥉"]
        for i, row in enumerate(rows, 1):
            prefix = medals[i - 1] if i <= 3 else f"{i}."
            lines.append(f"{prefix} {display_name(row)} — {row['weekly_points']} tšk.")
    lines.extend([
        "",
        "🔄 Reset: pirmadienį 00:00",
        "🎯 Spausk „DALYVAUTI“ ir gauk savo invite nuorodą.",
        "",
        f"🟢 Atnaujinta: {now:%H:%M}",
    ])
    return "\n".join(lines)


def live_leaderboard_markup(application: Application):
    username = application.bot_data.get("bot_username")
    buttons = []
    if username:
        buttons.append([InlineKeyboardButton("🎯 DALYVAUTI / GAUTI INVITE", url=f"https://t.me/{username}")])
    if GROUP_PUBLIC_URL:
        buttons.append([InlineKeyboardButton("🇺🇸 NĖRA DROPO", url=GROUP_PUBLIC_URL)])
    return InlineKeyboardMarkup(buttons) if buttons else None


async def refresh_live_leaderboard(application: Application, force_new: bool = False):
    if not LIVE_LEADERBOARD_ENABLED:
        return None
    group_id = application.bot_data.get("group_id")
    if not group_id:
        return None
    text = live_leaderboard_text()
    markup = live_leaderboard_markup(application)
    raw = get_setting("live_leaderboard_message_id", "")
    try:
        message_id = int(raw) if raw else None
    except ValueError:
        message_id = None

    if message_id and not force_new:
        try:
            await application.bot.edit_message_text(chat_id=group_id, message_id=message_id, text=text, reply_markup=markup)
            return message_id
        except Exception as e:
            if "not modified" in str(e).lower():
                return message_id
            log.warning("LIVE TOP edit nepavyko, kuriamas naujas postas: %s", e)

    msg = await application.bot.send_message(chat_id=group_id, text=text, reply_markup=markup)
    set_setting("live_leaderboard_message_id", str(msg.message_id))
    return msg.message_id


async def live_leaderboard_loop(application: Application):
    while True:
        try:
            if get_setting("live_leaderboard_enabled", "1") == "1":
                await refresh_live_leaderboard(application)
        except Exception:
            log.exception("LIVE TOP 10 atnaujinimo klaida")
        await asyncio.sleep(LIVE_LEADERBOARD_REFRESH_SECONDS)


async def liveboard_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_ads_admin(update, context):
        return
    set_setting("live_leaderboard_enabled", "1")
    mid = await refresh_live_leaderboard(context.application)
    await update.effective_message.reply_text(f"✅ LIVE TOP 10 įjungtas. Message ID: {mid}")


async def liveboardnew_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_ads_admin(update, context):
        return
    set_setting("live_leaderboard_enabled", "1")
    mid = await refresh_live_leaderboard(context.application, force_new=True)
    await update.effective_message.reply_text(f"✅ Sukurtas naujas LIVE TOP 10 postas. ID: {mid}")


async def liveboardoff_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_ads_admin(update, context):
        return
    set_setting("live_leaderboard_enabled", "0")
    await update.effective_message.reply_text("🔴 LIVE TOP 10 automatinis atnaujinimas išjungtas.")


async def topad_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_ads_admin(update, context):
        return
    group_id = await ensure_group(context)
    msg = await context.bot.send_message(chat_id=group_id, text=live_leaderboard_text(), reply_markup=live_leaderboard_markup(context.application))
    await update.effective_message.reply_text(f"✅ TOP 10 reklaminis postas įkeltas į grupę. ID: {msg.message_id}")

def menu():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🔗 Mano invite", callback_data="my_link"),
                InlineKeyboardButton("⭐ Mano taškai", callback_data="points"),
            ],
            [
                InlineKeyboardButton("🏆 Savaitės TOP", callback_data="top"),
                InlineKeyboardButton("📊 Viso TOP", callback_data="alltime"),
            ],
            [
                InlineKeyboardButton("🥇 Praeita savaitė", callback_data="lastweek"),
                InlineKeyboardButton("ℹ️ Kaip veikia", callback_data="info"),
            ],
        ]
    )


async def ensure_group(context: ContextTypes.DEFAULT_TYPE):
    if "group_id" not in context.application.bot_data:
        chat = await context.bot.get_chat(GROUP_CHAT_RAW)
        context.application.bot_data["group_id"] = chat.id
        context.application.bot_data["group_title"] = chat.title or str(chat.id)
    return context.application.bot_data["group_id"]


async def ensure_personal_link(context: ContextTypes.DEFAULT_TYPE, user):
    upsert_user(user)
    row = get_user(user.id)

    if row and row["invite_link"]:
        return row["invite_link"]

    group_id = await ensure_group(context)

    link_obj = await context.bot.create_chat_invite_link(
        chat_id=group_id,
        name=f"ref_{user.id}"[:32],
    )

    save_invite_link(user.id, link_obj.invite_link)
    return link_obj.invite_link


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    upsert_user(update.effective_user)
    await ensure_group(context)
    title = context.application.bot_data.get("group_title", "grupė")

    text = (
        f"👋 {title} savaitinis invite konkursas.\n\n"
        "🔗 Gauk savo asmeninę invite nuorodą.\n"
        "👤 Kiekvienas naujas žmogus = +1 taškas.\n"
        "🏆 Savaitės leaderboard resetinamas kiekvieną pirmadienį 00:00.\n\n"
        "Tas pats žmogus pakartotinai taško nebeduoda."
    )

    await update.effective_message.reply_text(text, reply_markup=menu())


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    user = q.from_user
    upsert_user(user)

    if q.data == "my_link":
        try:
            link = await ensure_personal_link(context, user)
            text = (
                "🔗 Tavo asmeninė invite nuoroda:\n\n"
                f"{link}\n\n"
                "Kiekvienas naujas žmogus, pirmą kartą prisijungęs per ją, = +1 savaitės taškas."
            )
        except Exception:
            log.exception("Nepavyko sukurti invite link")
            text = (
                "Nepavyko sukurti invite nuorodos.\n\n"
                "Patikrink, ar botas yra grupės administratorius ir turi teisę kviesti vartotojus."
            )

        await q.edit_message_text(text, reply_markup=menu())
        return

    if q.data == "points":
        row = get_user(user.id)
        weekly = int(row["weekly_points"]) if row else 0
        total = int(row["points"]) if row else 0

        await q.edit_message_text(
            f"⭐ Šią savaitę: {weekly} tšk.\n📊 Iš viso: {total} tšk.",
            reply_markup=menu(),
        )
        return

    if q.data == "top":
        rows = weekly_top()
        lines = [f"🏆 SAVAITĖS TOP — {current_week_key()}"]

        if not rows:
            lines.append("\nKol kas taškų nėra.")
        else:
            for i, row in enumerate(rows, 1):
                lines.append(
                    f"{i}. {display_name(row)} — {row['weekly_points']} tšk."
                )

        lines.append("\n🔄 Reset: pirmadienį 00:00")
        await q.edit_message_text("\n".join(lines), reply_markup=menu())
        return

    if q.data == "alltime":
        rows = alltime_top()
        lines = ["📊 VISO LAIKO TOP"]

        if not rows:
            lines.append("\nKol kas taškų nėra.")
        else:
            for i, row in enumerate(rows, 1):
                lines.append(f"{i}. {display_name(row)} — {row['points']} tšk.")

        await q.edit_message_text("\n".join(lines), reply_markup=menu())
        return

    if q.data == "lastweek":
        wk, rows = last_week_top()
        if not rows:
            text = "🥇 Praėjusios savaitės rezultatų dar nėra."
        else:
            lines = [f"🥇 PRAĖJUSI SAVAITĖ — {wk}"]
            for i, row in enumerate(rows, 1):
                lines.append(f"{i}. {display_name(row)} — {row['points']} tšk.")
            text = "\n".join(lines)

        await q.edit_message_text(text, reply_markup=menu())
        return

    if q.data == "info":
        text = (
            "ℹ️ Kaip veikia:\n\n"
            "1. Spausk „Mano invite“.\n"
            "2. Dalinkis savo unikalia grupės nuoroda.\n"
            "3. Naujas žmogus prisijungia — +1 taškas.\n"
            "4. Savaitės TOP resetinamas pirmadienį 00:00.\n"
            "5. Bendras viso laiko taškų skaičius nedingsta.\n\n"
            "Jei žmogų į grupę tiesiogiai prideda kitas narys, sistema taip pat bando užskaityti +1 pridėjusiam nariui."
        )
        await q.edit_message_text(text, reply_markup=menu())


async def mylink_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        link = await ensure_personal_link(context, update.effective_user)
        await update.effective_message.reply_text(
            f"🔗 Tavo invite nuoroda:\n{link}"
        )
    except Exception:
        log.exception("Invite link klaida")
        await update.effective_message.reply_text(
            "Nepavyko sukurti nuorodos. Botui reikia admin teisių ir teisės kviesti vartotojus."
        )


async def points_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    upsert_user(update.effective_user)
    row = get_user(update.effective_user.id)

    weekly = int(row["weekly_points"]) if row else 0
    total = int(row["points"]) if row else 0

    await update.effective_message.reply_text(
        f"⭐ Šią savaitę: {weekly} tšk.\n📊 Iš viso: {total} tšk."
    )


async def top_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = weekly_top()
    lines = [f"🏆 SAVAITĖS TOP — {current_week_key()}"]

    if not rows:
        lines.append("Kol kas tuščia.")
    else:
        for i, row in enumerate(rows, 1):
            lines.append(
                f"{i}. {display_name(row)} — {row['weekly_points']} tšk."
            )

    lines.append("\n🔄 Reset: pirmadienį 00:00")
    await update.effective_message.reply_text("\n".join(lines))


async def alltime_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = alltime_top()
    lines = ["📊 VISO LAIKO TOP"]

    if not rows:
        lines.append("Kol kas tuščia.")
    else:
        for i, row in enumerate(rows, 1):
            lines.append(f"{i}. {display_name(row)} — {row['points']} tšk.")

    await update.effective_message.reply_text("\n".join(lines))


async def lastweek_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    wk, rows = last_week_top()

    if not rows:
        await update.effective_message.reply_text(
            "Praėjusios savaitės rezultatų dar nėra."
        )
        return

    lines = [f"🥇 PRAĖJUSI SAVAITĖ — {wk}"]
    for i, row in enumerate(rows, 1):
        lines.append(f"{i}. {display_name(row)} — {row['points']} tšk.")

    await update.effective_message.reply_text("\n".join(lines))


async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_ads_admin(update, context):
        return

    ensure_current_week()

    with closing(db()) as conn:
        total_users = conn.execute(
            "SELECT COUNT(*) c FROM users"
        ).fetchone()["c"]
        total_refs = conn.execute(
            "SELECT COUNT(*) c FROM referrals"
        ).fetchone()["c"]
        week_refs = conn.execute(
            "SELECT COALESCE(SUM(weekly_points), 0) c FROM users"
        ).fetchone()["c"]

    await update.effective_message.reply_text(
        f"📊 Sistema\n"
        f"Vartotojų DB: {total_users}\n"
        f"Visų laikų invite/add: {total_refs}\n"
        f"Šios savaitės taškai: {week_refs}\n"
        f"Savaitė: {current_week_key()}"
    )



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
        idx = int(get_setting("ads_next_index", "0"))
    except ValueError:
        idx = 0

    next_note = notes[idx % len(notes)] if notes else "—"

    return (
        "📢 ROSE ADS ROTACIJA\n\n"
        f"Būsena: {'🟢 ĮJUNGTA' if enabled else '🔴 IŠJUNGTA'}\n"
        f"Intervalas: kas {interval} min.\n"
        f"Rose notes: {', '.join(notes) if notes else 'nėra'}\n"
        f"Kitas: {next_note}\n\n"
        "Valdymas:\n"
        "/adsset ads konkursas promo\n"
        "/adsinterval 10\n"
        "/adson\n"
        "/adsoff\n"
        "/adsnext"
    )


async def user_can_control_ads(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user = update.effective_user
    if not user:
        return False

    # Explicit ADMIN_IDS visada leidžiami.
    if user.id in ADMIN_IDS:
        return True

    # Jei ADMIN_IDS neužpildytas arba žmogus ne sąraše,
    # leidžiame tik tikriems grupės admin/owner.
    try:
        group_id = await ensure_group(context)
        member = await context.bot.get_chat_member(group_id, user.id)
        return member.status in {
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.OWNER,
        }
    except Exception:
        return False


async def require_ads_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    ok = await user_can_control_ads(update, context)
    if not ok and update.effective_message:
        await update.effective_message.reply_text(
            "⛔ Šį ADS valdymą gali keisti tik grupės administratorius."
        )
    return ok


async def ads_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_ads_admin(update, context):
        return
    await update.effective_message.reply_text(ads_status_text())


async def adsset_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_ads_admin(update, context):
        return

    notes = []
    for raw in context.args:
        note = raw.strip().lstrip("#")
        if not note:
            continue
        # Rose note vardas turi būti vienas žodis.
        if not re.fullmatch(r"[A-Za-z0-9_\\-]{1,64}", note):
            await update.effective_message.reply_text(
                f"❌ Netinkamas note vardas: {note}\n"
                "Naudok, pvz.: /adsset ads konkursas promo"
            )
            return
        if note not in notes:
            notes.append(note)

    if not notes:
        await update.effective_message.reply_text(
            "Naudojimas:\n/adsset ads konkursas promo"
        )
        return

    set_setting("ads_notes", json.dumps(notes, ensure_ascii=False))
    set_setting("ads_next_index", "0")

    await update.effective_message.reply_text(
        "✅ ADS rotacija atnaujinta:\n"
        + " → ".join(notes)
        + "\n\nDabar intervalą gali keisti su /adsinterval 10"
    )


async def adsinterval_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_ads_admin(update, context):
        return

    if not context.args:
        await update.effective_message.reply_text(
            "Naudojimas: /adsinterval 10\n"
            "Skaičius = minutės."
        )
        return

    try:
        minutes = int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text(
            "❌ Intervalas turi būti skaičius, pvz. /adsinterval 10"
        )
        return

    # Sąmoningai neduodame labai agresyvaus intervalo.
    if minutes < 5 or minutes > 1440:
        await update.effective_message.reply_text(
            "❌ Galimas intervalas: nuo 5 iki 1440 min."
        )
        return

    set_setting("ads_interval_minutes", str(minutes))

    await update.effective_message.reply_text(
        f"✅ ADS intervalas pakeistas: kas {minutes} min."
    )


async def adson_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_ads_admin(update, context):
        return

    if not get_ads_notes():
        await update.effective_message.reply_text(
            "Pirma pridėk Rose notes, pvz.:\n/adsset ads konkursas"
        )
        return

    set_setting("ads_enabled", "1")
    set_setting("ads_force_send", "1")

    await update.effective_message.reply_text(
        "🟢 ADS rotacija įjungta. Pirmas postas bus iškviestas netrukus."
    )


async def adsoff_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_ads_admin(update, context):
        return

    set_setting("ads_enabled", "0")
    set_setting("ads_force_send", "0")

    await update.effective_message.reply_text(
        "🔴 ADS rotacija išjungta."
    )


async def adsnext_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_ads_admin(update, context):
        return

    if get_setting("ads_enabled", "0") != "1":
        await update.effective_message.reply_text(
            "ADS rotacija dabar išjungta. Pirma naudok /adson."
        )
        return

    set_setting("ads_force_send", "1")
    await update.effective_message.reply_text(
        "⏭ Kitas ADS bus paleistas netrukus."
    )

def became_member(old_status: str, new_status: str) -> bool:
    old_in = old_status in {
        ChatMemberStatus.MEMBER,
        ChatMemberStatus.ADMINISTRATOR,
        ChatMemberStatus.OWNER,
        ChatMemberStatus.RESTRICTED,
    }
    new_in = new_status in {
        ChatMemberStatus.MEMBER,
        ChatMemberStatus.ADMINISTRATOR,
        ChatMemberStatus.OWNER,
        ChatMemberStatus.RESTRICTED,
    }
    return (not old_in) and new_in


async def on_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cmu = update.chat_member
    if not cmu:
        return

    group_id = await ensure_group(context)

    if cmu.chat.id != group_id:
        return

    joined_user = cmu.new_chat_member.user

    if not became_member(
        cmu.old_chat_member.status,
        cmu.new_chat_member.status,
    ):
        return

    if joined_user.is_bot:
        return

    inviter_id = None
    source = None

    # Patikimiausia: žmogus įėjo per konkretaus nario unikalią boto invite nuorodą.
    if cmu.invite_link:
        inviter_id = owner_by_link(cmu.invite_link.invite_link)
        if inviter_id:
            source = "ref_link"

    # Tiesioginis Add Member.
    # Join request patvirtinusiam adminui taško nepriskiriame.
    if (
        inviter_id is None
        and not getattr(cmu, "via_join_request", False)
        and not getattr(cmu, "via_chat_folder_invite_link", False)
        and cmu.from_user
        and cmu.from_user.id != joined_user.id
        and not cmu.from_user.is_bot
    ):
        inviter_id = cmu.from_user.id
        source = "direct_add"
        upsert_user(cmu.from_user)

    if not inviter_id:
        return

    awarded = award_once(
        group_id=group_id,
        joined_user_id=joined_user.id,
        inviter_user_id=inviter_id,
        source=source,
    )

    if not awarded:
        return

    inviter = get_user(inviter_id)
    weekly = int(inviter["weekly_points"]) if inviter else 0
    total = int(inviter["points"]) if inviter else 0

    # Telegram botai gali PM rašyti tik tiems, kurie jau pradėjo botą.
    try:
        await context.bot.send_message(
            chat_id=inviter_id,
            text=(
                f"✅ +1 taškas!\n"
                f"⭐ Šią savaitę: {weekly}\n"
                f"📊 Iš viso: {total}"
            ),
        )
    except Exception:
        pass

    log.info(
        "Užskaitytas +1: inviter=%s joined=%s source=%s",
        inviter_id,
        joined_user.id,
        source,
    )

    try:
        if get_setting("live_leaderboard_enabled", "1") == "1":
            await refresh_live_leaderboard(context.application)
    except Exception:
        log.exception("Nepavyko iškart atnaujinti LIVE TOP 10")


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
                            lines.append("")
                            lines.append("🏆 TOP 3:")
                            for i, row in enumerate(previous_top[:3], 1):
                                lines.append(
                                    f"{i}. {display_name(row)} — {row['weekly_points']} tšk."
                                )

                        lines.append("")
                        lines.append("🔄 Nauja savaitė prasidėjo — taškai vėl nuo 0.")
                        lines.append("🎯 /start bote → gauk savo invite nuorodą.")

                        try:
                            await application.bot.send_message(
                                chat_id=group_id,
                                text="\n".join(lines),
                            )
                        except Exception:
                            log.exception("Nepavyko paskelbti savaitės reseto grupėje.")

                try:
                    await refresh_live_leaderboard(application)
                except Exception:
                    log.exception("Nepavyko atnaujinti LIVE TOP po savaitės reseto.")

        except Exception:
            log.exception("Weekly reset loop klaida")

        await asyncio.sleep(60)


async def post_init(application: Application):
    chat = await application.bot.get_chat(GROUP_CHAT_RAW)
    application.bot_data["group_id"] = chat.id
    application.bot_data["group_title"] = chat.title or str(chat.id)

    ensure_current_week()

    application.bot_data["_weekly_reset_task"] = asyncio.create_task(
        weekly_reset_loop(application)
    )

    me = await application.bot.get_me()
    application.bot_data["bot_username"] = me.username

    if LIVE_LEADERBOARD_ENABLED:
        application.bot_data["_live_leaderboard_task"] = asyncio.create_task(live_leaderboard_loop(application))
        try:
            await refresh_live_leaderboard(application)
        except Exception:
            log.exception("Nepavyko sukurti LIVE TOP 10 posto paleidimo metu.")

    log.info(
        "Botas @%s prijungtas prie %s (%s)",
        me.username,
        chat.title,
        chat.id,
    )


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

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("mylink", mylink_cmd))
    app.add_handler(CommandHandler("points", points_cmd))
    app.add_handler(CommandHandler("top", top_cmd))
    app.add_handler(CommandHandler("alltime", alltime_cmd))
    app.add_handler(CommandHandler("lastweek", lastweek_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))

    # Rose ADS rotacijos administravimo komandos.
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
    app.add_handler(
        ChatMemberHandler(
            on_chat_member,
            ChatMemberHandler.CHAT_MEMBER,
        )
    )

    print("Referral botas paleistas.")
    print(f"Savaitės resetas: pirmadienį 00:00 ({WEEK_TIMEZONE})")
    print("CTRL+C sustabdyti.")

    app.run_polling(
        allowed_updates=[
            "message",
            "callback_query",
            "chat_member",
            "my_chat_member",
        ],
        drop_pending_updates=False,
    )


if __name__ == "__main__":
    main()
