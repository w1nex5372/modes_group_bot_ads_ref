import asyncio
import json
import os
import sqlite3
import time
from contextlib import closing
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from telethon import TelegramClient, errors
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import RetryAfter
import ads_builder
import top3_ads

load_dotenv()

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"].strip()
SESSION_NAME = os.getenv("SESSION_NAME", "tg_autoforward").strip()

MAIN_GROUP = os.getenv("GROUP", os.getenv("GROUP_CHAT", "@NERADAUDROPO")).strip()
SOURCE_CHAT_RAW = os.getenv("SOURCE_CHAT", MAIN_GROUP).strip() or MAIN_GROUP
SOURCE_MESSAGE_ID = int(os.getenv("SOURCE_MESSAGE_ID", "0") or "0")
INTERVAL_SECONDS = int(os.getenv("INTERVAL_SECONDS", "3600"))
PER_CHAT_DELAY_SECONDS = int(os.getenv("PER_CHAT_DELAY_SECONDS", "5"))
CHATS_FILE = os.getenv("CHATS_FILE", "chats.txt").strip()

ROSE_ADS_CHAT_RAW = os.getenv("ROSE_ADS_CHAT", MAIN_GROUP).strip() or MAIN_GROUP
ROSE_BOT_USERNAME = os.getenv("ROSE_BOT_USERNAME", "").strip().lstrip("@")
ROSE_COMMAND_DELETE_DELAY_SECONDS = int(os.getenv("ROSE_COMMAND_DELETE_DELAY_SECONDS", "3"))
ROSE_FAST_NOTES = [x.strip() for x in os.getenv("ROSE_FAST_NOTES", "konkursas,promo").split(",") if x.strip()]
ROSE_FAST_INTERVAL_DEFAULT = max(5, int(os.getenv("ROSE_FAST_INTERVAL_MINUTES", "15")))
ROSE_MIN_AD_GAP_SECONDS = max(10, int(os.getenv("ROSE_MIN_AD_GAP_SECONDS", "120")))

DB_PATH = os.getenv("DB_PATH", "referrals.sqlite3").strip()


def parse_peer(value: str):
    value = value.strip()
    if value.lstrip("-").isdigit():
        return int(value)
    return value


SOURCE_CHAT = parse_peer(SOURCE_CHAT_RAW)
ROSE_ADS_CHAT = parse_peer(ROSE_ADS_CHAT_RAW)


def settings_db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init_settings():
    with closing(settings_db()) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
        defaults = {
            "ads_enabled": "0",
            "ads_interval_minutes": "30",
            "ads_fast_interval_minutes": str(ROSE_FAST_INTERVAL_DEFAULT),
            "ads_notes": "[]",
            "ads_next_index": "0",
            "ads_fast_index": "0",
            "ads_normal_index": "0",
            "ads_last_sent_ts": "0",
            "ads_fast_last_sent_ts": "0",
            "ads_normal_last_sent_ts": "0",
            "ads_force_send": "0",
            "ads_force_job_id": "0",
            "ads_last_note": "",
            "ads_last_lane": "",
            "ads_last_result": "never",
            "ads_last_verified": "0",
            "ads_last_error": "",
            "ads_last_response_id": "",
            "ads_scheduler_initialized": "0",
            "ads_top3_enabled": "0",
            "ads_top3_interval_minutes": "360",
            "ads_top3_last_sent_ts": "0",
            "ads_top3_last_success_ts": "0",
            "ads_top3_last_result": "never",
            "ads_top3_last_error": "",
            "ads_top3_force_send": "0",
        }
        for key, value in defaults.items():
            conn.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (key, value))
        marker = conn.execute("SELECT value FROM settings WHERE key='ads_transport_mode'").fetchone()
        if not marker or marker["value"] != "native_v1":
            # Explicit review after the Rose -> native cutover; never auto-post
            # merely because an old scheduler flag happened to be ON.
            for key, value in (
                ("ads_enabled", "0"), ("ads_force_send", "0"),
                ("ads_force_job_id", "0"), ("ads_notes", "[]"),
                ("ads_scheduler_initialized", "0"),
                ("ads_last_result", "native_ready"), ("ads_last_error", ""),
                ("ads_transport_mode", "native_v1"),
            ):
                conn.execute(
                    "INSERT INTO settings(key,value) VALUES(?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value),
                )
        conn.commit()


def get_setting(key, default=None):
    with closing(settings_db()) as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key, value):
    with closing(settings_db()) as conn:
        conn.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )
        conn.commit()


def safe_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def safe_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def stamp(ts=None):
    return datetime.fromtimestamp(ts or time.time()).strftime("%H:%M:%S")


def get_ads_notes():
    try:
        raw = json.loads(get_setting("ads_notes", "[]"))
    except Exception:
        raw = []
    out = []
    for note in raw if isinstance(raw, list) else []:
        note = str(note).strip()
        if note and note not in out:
            out.append(note)
    return out


def split_ads_notes():
    all_notes = get_ads_notes()
    fast_names = set(ROSE_FAST_NOTES)
    return (
        [n for n in all_notes if n in fast_names],
        [n for n in all_notes if n not in fast_names],
    )


def load_targets():
    path = Path(CHATS_FILE)
    if not path.exists():
        return []
    targets, seen = [], set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("https://t.me/"):
            line = "@" + line.split("https://t.me/", 1)[1].split("/", 1)[0]
        elif line.startswith("http://t.me/"):
            line = "@" + line.split("http://t.me/", 1)[1].split("/", 1)[0]
        peer = parse_peer(line)
        if str(peer) not in seen:
            seen.add(str(peer))
            targets.append(peer)
    return targets


async def forward_round(client):
    if SOURCE_MESSAGE_ID <= 0:
        return
    targets = load_targets()
    if not targets:
        return
    print(f"\n--- Auto-forward: {len(targets)} grupių ---")
    for index, target in enumerate(targets, 1):
        try:
            await client.forward_messages(target, SOURCE_MESSAGE_ID, from_peer=SOURCE_CHAT)
            print(f"[{index}/{len(targets)}] OK -> {target}")
        except errors.FloodWaitError as e:
            print(f"FLOOD_WAIT {e.seconds}s")
            await asyncio.sleep(e.seconds + 5)
        except errors.ChatWriteForbiddenError:
            print(f"[{index}/{len(targets)}] NEGALIMA RAŠYTI -> {target}")
        except Exception as e:
            print(f"[{index}/{len(targets)}] KLAIDA -> {target}: {type(e).__name__}: {e}")
        if index < len(targets):
            await asyncio.sleep(PER_CHAT_DELAY_SECONDS)


async def forward_loop(client):
    while True:
        try:
            await forward_round(client)
        except Exception as e:
            print(f"Forward klaida: {type(e).__name__}: {e}")
        await asyncio.sleep(INTERVAL_SECONDS)


def rose_get_command(note):
    return f"/get@{ROSE_BOT_USERNAME} {note}" if ROSE_BOT_USERNAME else f"/get {note}"


async def verify_rose_response(client, sent_id):
    try:
        await asyncio.sleep(1)
        messages = await client.get_messages(ROSE_ADS_CHAT, limit=15)
        wanted = ROSE_BOT_USERNAME.lower()
        for msg in messages:
            if not msg or msg.id <= sent_id:
                continue
            sender = await msg.get_sender()
            if not sender or not getattr(sender, "bot", False):
                continue
            username = (getattr(sender, "username", "") or "").lower()
            if wanted:
                if username != wanted:
                    continue
            elif getattr(msg, "reply_to_msg_id", None) != sent_id:
                continue
            body = str(getattr(msg, "raw_text", None) or getattr(msg, "message", "") or "").strip().casefold()
            if body.startswith("note not found"):
                return "missing_note", msg.id
            return "ok", msg.id
        return "unverified", None
    except Exception:
        return "unverified", None


async def wait_builder_rose_reply(client, command_id, kind, expected_text, expected_buttons=None):
    """Require a Rose reply to our exact /get, not an unrelated bot message."""
    for _ in range(12):
        messages = await client.get_messages(ROSE_ADS_CHAT, limit=20)
        for msg in messages:
            if not msg or msg.id <= command_id or getattr(msg, "reply_to_msg_id", None) != command_id:
                continue
            sender = await msg.get_sender()
            if not sender or (getattr(sender, "username", "") or "").casefold() != (ROSE_BOT_USERNAME or "MissRose_bot").casefold():
                continue
            body = (msg.raw_text or "").strip()
            if body.casefold().startswith("note not found"):
                return "missing_note", msg.id
            if kind in {"photo", "video"} and not msg.media:
                return "wrong_content", msg.id
            if kind != "delete" and expected_text[:16].casefold() not in body.casefold():
                return "wrong_content", msg.id
            if expected_buttons:
                actual = rose_reply_buttons(msg)
                if any((button["label"], button["url"]) not in actual for button in expected_buttons):
                    return "wrong_buttons", msg.id
            return "ok", msg.id
        await asyncio.sleep(1)
    return "unverified", None


def rose_reply_buttons(msg):
    """Read both Telethon's wrapper and Telegram's raw inline markup."""
    actual = set()
    for row in (getattr(msg, "buttons", None) or []):
        for button in row:
            raw = getattr(button, "button", button)
            label = getattr(button, "text", None) or getattr(raw, "text", None)
            url = (getattr(button, "url", None) or getattr(raw, "url", None)
                   or getattr(getattr(raw, "type", None), "url", None))
            if label and url:
                actual.add((label, url))
    markup = getattr(msg, "reply_markup", None)
    for row in (getattr(markup, "rows", None) or []):
        for button in (getattr(row, "buttons", None) or []):
            label = getattr(button, "text", None)
            url = getattr(button, "url", None) or getattr(getattr(button, "type", None), "url", None)
            if label and url:
                actual.add((label, url))
    return actual


async def reconcile_uncertain_rose_buttons(client):
    """One-off readback for jobs falsely flagged by the old button parser."""
    if get_setting("ads_builder_reconcile_v2_once", "0") == "1":
        return
    set_setting("ads_builder_reconcile_v2_once", "1")
    with closing(settings_db()) as conn:
        jobs = [dict(row) for row in conn.execute(
            "SELECT j.* FROM ads_builder_jobs j WHERE j.status='uncertain' "
            "AND j.operation='save' AND j.error LIKE '%wrong_buttons%' "
            "AND NOT EXISTS (SELECT 1 FROM ads_builder_jobs later "
            "WHERE later.note=j.note AND later.id>j.id) ORDER BY j.id LIMIT 5"
        ).fetchall()]
    for job in jobs:
        command = response_id = None
        try:
            command = await client.send_message(ROSE_ADS_CHAT, rose_get_command(job["note"]))
            result, response_id = await wait_builder_rose_reply(
                client, command.id, job["kind"], job["body"], json.loads(job["buttons_json"]),
            )
            confirmed = result == "ok" and ads_builder.reconcile_verified(DB_PATH, job["id"])
            print(f"ADS reconcile #{job['id']} {job['note']}: {'verified' if confirmed else result}")
            if result == "unverified":
                recent = await client.get_messages(ROSE_ADS_CHAT, limit=20)
                observed = []
                for msg in recent:
                    if not msg or msg.id <= command.id:
                        continue
                    sender = await msg.get_sender()
                    is_rose = (getattr(sender, "username", "") or "").casefold() == (ROSE_BOT_USERNAME or "MissRose_bot").casefold()
                    observed.append((msg.id, "Rose" if is_rose else "other", msg.reply_to_msg_id))
                print(f"ADS reconcile #{job['id']} response metadata: {observed[:8]}")
        except Exception as exc:
            print(f"ADS reconcile #{job['id']} {job['note']}: {type(exc).__name__}")
        finally:
            cleanup = [mid for mid in (getattr(command, "id", None), response_id) if mid]
            if cleanup:
                try:
                    await client.delete_messages(ROSE_ADS_CHAT, cleanup, revoke=True)
                except Exception:
                    pass


async def process_ads_builder_job(client, bot, job):
    """Save from the existing user session; bot-to-bot /save is not reliable."""
    draft_message_id = None
    save_message_id = None
    get_message_id = None
    rose_response_id = None
    status = "uncertain"
    error = "Rose rezultato nepavyko patvirtinti. Patikrink rankiniu būdu."
    try:
        rose_username = ROSE_BOT_USERNAME or "MissRose_bot"
        rose = await bot.get_chat(f"@{rose_username}")
        member = await bot.get_chat_member(ROSE_ADS_CHAT, rose.id)
        if member.status not in {"administrator", "creator"}:
            raise RuntimeError("Rose is not a group administrator")
        if job["operation"] == "delete":
            save = await client.send_message(ROSE_ADS_CHAT, f"/clear@{rose_username} {job['note']}")
            save_message_id = save.id
        else:
            buttons = json.loads(job["buttons_json"])
            rendered = ads_builder.validate_size(job["kind"], job["body"], buttons, job["note"])
            if job["kind"] == "text":
                save = await client.send_message(ROSE_ADS_CHAT, f"/save@{rose_username} {job['note']} {rendered}")
                save_message_id = save.id
            else:
                copied = await bot.copy_message(
                    chat_id=ROSE_ADS_CHAT,
                    from_chat_id=job["source_chat_id"] or job["admin_id"],
                    message_id=job["source_message_id"], caption=rendered,
                )
                draft_message_id = copied.message_id
                save = await client.send_message(
                    ROSE_ADS_CHAT, f"/save@{rose_username} {job['note']}", reply_to=draft_message_id,
                )
                save_message_id = save.id
        await asyncio.sleep(3)
        get = await client.send_message(ROSE_ADS_CHAT, f"/get@{rose_username} {job['note']}")
        get_message_id = get.id
        result, rose_response_id = await wait_builder_rose_reply(
            client, get.id, "delete" if job["operation"] == "delete" else job["kind"], job["body"],
            [] if job["operation"] == "delete" else json.loads(job["buttons_json"]),
        )
        if job["operation"] == "delete":
            if result == "missing_note":
                status, error = "deleted", ""
            else:
                status, error = "uncertain", f"Rose užrašo ištrynimas nepatvirtintas ({result})."
        elif result == "ok":
            status, error = "verified", ""
        elif result == "missing_note":
            status, error = "failed", "Rose nerado užrašo. Patikrink, ar Telegram naudotojas yra grupės adminas."
        else:
            status, error = "uncertain", f"Rose atsakymas nepatvirtintas ({result}). Patikrink rankiniu būdu."
    except Exception as exc:
        # The save may already have happened, so never auto-retry.
        status = "uncertain" if save_message_id else "failed"
        error = f"Nepavyko perduoti reklamos ({type(exc).__name__}). Patikrink Rose rankiniu būdu."
    finally:
        cleanup = [mid for mid in (draft_message_id, save_message_id, get_message_id) if mid]
        if save_message_id:
            try:
                for message in await client.get_messages(ROSE_ADS_CHAT, limit=30):
                    if getattr(message, "reply_to_msg_id", None) != save_message_id:
                        continue
                    sender = await message.get_sender()
                    if (getattr(sender, "username", "") or "").casefold() == (ROSE_BOT_USERNAME or "MissRose_bot").casefold():
                        cleanup.append(message.id)
            except Exception:
                pass
        if cleanup:
            try:
                await client.delete_messages(ROSE_ADS_CHAT, cleanup, revoke=True)
            except Exception:
                pass
        if rose_response_id and (not job["launch_now"] or status != "verified"):
            try:
                await client.delete_messages(ROSE_ADS_CHAT, [rose_response_id], revoke=True)
            except Exception:
                pass
        ads_builder.finish(DB_PATH, job["id"], status, error)
        if status == "deleted":
            for setting_key in ("ads_notes", "ads_builder_selected_notes", "ads_builder_known_notes"):
                try:
                    names = json.loads(get_setting(setting_key, "[]"))
                    if isinstance(names, list):
                        set_setting(setting_key, json.dumps([n for n in names if n != job["note"]], ensure_ascii=False))
                except Exception:
                    pass
            message = f"🗑 ADS #{job['id']} ({job['note']}) ištrintas iš Rose ir AUTO sąrašo."
        elif status == "verified":
            message = (
                f"✅ ADS #{job['id']} ({job['note']}) išsaugotas Rose ir patikrintas."
                + (" Vieną kartą paleistas grupėje." if job["launch_now"] else "")
                + "\nAutomatinė rotacija liko OFF. Jei reikia, įjunk ADS ON."
            )
        else:
            message = f"⚠️ ADS #{job['id']} ({job['note']}): {error}\nAutomatinė rotacija OFF."
        try:
            await bot.send_message(chat_id=job["admin_id"], text=message)
        except Exception:
            pass


async def ads_builder_loop(client):
    ads_builder.init(DB_PATH)
    ads_builder.recover_after_worker_restart(DB_PATH)
    async with Bot(os.environ["BOT_TOKEN"]) as bot:
        while True:
            try:
                job = ads_builder.claim(DB_PATH)
                if job:
                    await process_ads_builder_job(client, bot, job)
                else:
                    await asyncio.sleep(3)
            except Exception as exc:
                print(f"ADS builder klaida: {type(exc).__name__}")
                await asyncio.sleep(5)


async def top3_ads_tick(bot):
    force = get_setting("ads_top3_force_send", "0") == "1"
    if get_setting("ads_top3_enabled", "0") != "1" and not force:
        return "off"
    now = time.time()
    interval = max(60, min(1440, safe_int(get_setting("ads_top3_interval_minutes", "360"), 360))) * 60
    last = safe_float(get_setting("ads_top3_last_sent_ts", "0"), 0)
    last_success = safe_float(get_setting("ads_top3_last_success_ts", "0"), 0)
    if last_success and now - last_success < ROSE_MIN_AD_GAP_SECONDS:
        return "gap"
    if not force and last and now - last < interval:
        return "waiting"
    last_rose = safe_float(get_setting("ads_last_sent_ts", "0"), 0)
    if last_rose and now - last_rose < ROSE_MIN_AD_GAP_SECONDS:
        return "gap"
    # Mark before send so a crash cannot immediately duplicate a post.
    set_setting("ads_top3_force_send", "0")
    set_setting("ads_top3_last_sent_ts", now)
    set_setting("ads_top3_last_result", "sending")
    admins = await bot.get_chat_administrators(ROSE_ADS_CHAT)
    excluded = {member.user.id for member in admins}
    excluded.update(int(item) for item in os.getenv("ADMIN_IDS", "").split(",") if item.strip().isdigit())
    rows = top3_ads.top3_rows(DB_PATH, excluded)
    body = top3_ads.render_top3(rows)
    if not body:
        set_setting("ads_top3_last_result", "empty")
        set_setting("ads_top3_last_error", "")
        return "empty"
    sent = await bot.send_message(
        chat_id=ROSE_ADS_CHAT, text=body, parse_mode="HTML",
        reply_markup=top3_ads.invite_markup(os.getenv("BOT_USERNAME", "")),
    )
    set_setting("ads_top3_last_result", "ok")
    set_setting("ads_top3_last_success_ts", now)
    set_setting("ads_top3_last_error", "")
    set_setting("ads_top3_last_message_id", sent.message_id)
    print(f"[{stamp()}] Weekly TOP 3 ADS sent | message {sent.message_id}")
    return "sent"


async def top3_ads_loop():
    """Publish a fresh, admin-filtered weekly ranking, independent of Rose notes."""
    async with Bot(os.environ["BOT_TOKEN"]) as bot:
        while True:
            try:
                await top3_ads_tick(bot)
            except RetryAfter as exc:
                set_setting("ads_top3_last_result", "rate_limited")
                set_setting("ads_top3_last_error", "Telegram rate limit")
                await asyncio.sleep(min(300, float(exc.retry_after) + 1))
            except Exception as exc:
                set_setting("ads_top3_last_result", "error")
                set_setting("ads_top3_last_error", type(exc).__name__)
                print(f"Weekly TOP 3 ADS error: {type(exc).__name__}")
                await asyncio.sleep(60)
            await asyncio.sleep(5)


def record_result(note, lane, result, verified=False, error="", response_id=None):
    now = time.time()
    set_setting("ads_last_sent_ts", now)
    set_setting("ads_last_note", note)
    set_setting("ads_last_lane", lane)
    set_setting("ads_last_result", result)
    set_setting("ads_last_verified", "1" if verified else "0")
    set_setting("ads_last_error", error)
    set_setting("ads_last_response_id", response_id or "")


async def send_direct_ad(bot, note, lane):
    """Publish one managed ad from our bot, with no Rose command in the group."""
    row = ads_builder.active_by_note(DB_PATH, note)
    if not row:
        set_setting("ads_enabled", "0")
        set_setting("ads_force_send", "0")
        record_result(note, lane, "missing_ad", False, "ADS no longer exists; AUTO stopped")
        return False
    buttons = json.loads(row["buttons_json"])
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton(button["label"], url=button["url"])] for button in buttons
    ]) if buttons else None
    common = {"chat_id": ROSE_ADS_CHAT, "reply_markup": markup, "disable_notification": True}
    try:
        if row["kind"] == "text":
            sent = await bot.send_message(text=row["body"], **common)
        elif row["media_file_id"]:
            if row["kind"] == "photo":
                sent = await bot.send_photo(photo=row["media_file_id"], caption=row["body"], **common)
            else:
                sent = await bot.send_video(video=row["media_file_id"], caption=row["body"], **common)
        else:
            sent = await bot.copy_message(
                from_chat_id=row["source_chat_id"] or row["admin_id"],
                message_id=row["source_message_id"], caption=row["body"], **common,
            )
    except Exception as exc:
        set_setting("ads_enabled", "0")
        set_setting("ads_force_send", "0")
        reason = f"{type(exc).__name__}: direct ADS send failed; AUTO stopped"
        record_result(note, lane, "error", False, reason)
        print(f"[{stamp()}] Direct ADS STOP | {lane.upper()} | {note} | {type(exc).__name__}")
        try:
            await bot.send_message(
                chat_id=row["admin_id"],
                text=f"⚠️ ADS „{note}“ nepavyko išsiųsti. AUTO sustabdytas. Jei tai sena media reklama, įkelk ją iš naujo per /ads.",
            )
        except Exception:
            pass
        return False
    now = time.time()
    if lane == "fast":
        set_setting("ads_fast_last_sent_ts", now)
    elif lane == "normal":
        set_setting("ads_normal_last_sent_ts", now)
    record_result(note, lane, "ok", True, response_id=sent.message_id)
    print(f"[{stamp()}] Direct ADS sent | {lane.upper()} | {note} | message {sent.message_id}")
    return True


def next_lane_note(lane):
    fast, normal = split_ads_notes()
    notes = fast if lane == "fast" else normal
    if not notes:
        return None
    key = "ads_fast_index" if lane == "fast" else "ads_normal_index"
    idx = safe_int(get_setting(key, "0"), 0)
    note = notes[idx % len(notes)]
    set_setting(key, (idx + 1) % len(notes))
    return note


def next_force_note():
    notes = get_ads_notes()
    if not notes:
        return None, None
    idx = safe_int(get_setting("ads_next_index", "0"), 0)
    note = notes[idx % len(notes)]
    set_setting("ads_next_index", (idx + 1) % len(notes))
    return note, "fast" if note in set(ROSE_FAST_NOTES) else "normal"


def initialize_scheduler():
    if get_setting("ads_scheduler_initialized", "0") == "1":
        return
    now = time.time()
    set_setting("ads_fast_last_sent_ts", now)
    set_setting("ads_normal_last_sent_ts", now)
    set_setting("ads_scheduler_initialized", "1")
    print(f"[{stamp(now)}] ADS scheduler start")


async def direct_ads_tick(bot):
    forced_id = safe_int(get_setting("ads_force_job_id", "0"), 0)
    if forced_id:
        # Clear before transport: a restart must not repeat a public ad.
        set_setting("ads_force_job_id", "0")
        row = ads_builder.active_by_id(DB_PATH, forced_id)
        if not row:
            set_setting("ads_last_error", "One-time ADS skipped: no longer verified")
            return "skipped"
        lane = "fast" if row["note"] in set(ROSE_FAST_NOTES) else "normal"
        return "sent" if await send_direct_ad(bot, row["note"], lane) else "failed"
    if get_setting("ads_enabled", "0") != "1":
        return "off"
    if not get_ads_notes():
        set_setting("ads_enabled", "0")
        set_setting("ads_force_send", "0")
        set_setting("ads_last_result", "no_notes")
        set_setting("ads_last_error", "AUTO stopped: no ads selected")
        print(f"[{stamp()}] Direct ADS STOP | no ads selected")
        return "empty"
    initialize_scheduler()
    fast_interval = max(5, min(1440, safe_int(get_setting("ads_fast_interval_minutes", ROSE_FAST_INTERVAL_DEFAULT), ROSE_FAST_INTERVAL_DEFAULT)))
    normal_interval = max(5, min(1440, safe_int(get_setting("ads_interval_minutes", "30"), 30)))
    fast_notes, normal_notes = split_ads_notes()
    now = time.time()
    last_top3 = safe_float(get_setting("ads_top3_last_success_ts", "0"), 0)
    last_any = max(safe_float(get_setting("ads_last_sent_ts", "0"), 0), last_top3)
    last_fast = safe_float(get_setting("ads_fast_last_sent_ts", "0"), 0)
    last_normal = safe_float(get_setting("ads_normal_last_sent_ts", "0"), 0)
    if get_setting("ads_force_send", "0") == "1":
        set_setting("ads_force_send", "0")
        note, lane = next_force_note()
        return "sent" if note and await send_direct_ad(bot, note, lane) else "skipped"
    if last_any and now - last_any < ROSE_MIN_AD_GAP_SECONDS:
        return "gap"
    fast_due = bool(fast_notes) and now - last_fast >= fast_interval * 60
    normal_due = bool(normal_notes) and now - last_normal >= normal_interval * 60
    if fast_due and normal_due:
        fast_over = now - last_fast - fast_interval * 60
        normal_over = now - last_normal - normal_interval * 60
        lane = "fast" if fast_over >= normal_over else "normal"
        note = next_lane_note(lane)
    elif fast_due:
        note, lane = next_lane_note("fast"), "fast"
    elif normal_due:
        note, lane = next_lane_note("normal"), "normal"
    else:
        return "waiting"
    return "sent" if note and await send_direct_ad(bot, note, lane) else "failed"


async def direct_ads_loop():
    print(f"Direct ADS group: {ROSE_ADS_CHAT}")
    async with Bot(os.environ["BOT_TOKEN"]) as bot:
        while True:
            try:
                await direct_ads_tick(bot)
            except RetryAfter as exc:
                set_setting("ads_last_error", "Telegram rate limit")
                await asyncio.sleep(min(300, float(exc.retry_after) + 1))
            except Exception as exc:
                set_setting("ads_enabled", "0")
                set_setting("ads_last_error", type(exc).__name__)
                print(f"Direct ADS STOP | {type(exc).__name__}")
                await asyncio.sleep(60)
            await asyncio.sleep(5)


async def main():
    init_settings()
    ads_builder.init(DB_PATH)
    client = TelegramClient(SESSION_NAME, API_ID, API_HASH)
    print("Jungiamasi prie Telegram user automation...")
    await client.start()
    me = await client.get_me()
    print(f"Prisijungta kaip: {me.first_name or ''} (@{me.username or 'be_username'})")
    print("FAST ADS intervalas valdomas /adsfast, NORMAL — /adsinterval")
    print("CTRL+C sustabdyti.\n")
    try:
        await asyncio.gather(forward_loop(client), direct_ads_loop(), top3_ads_loop())
    finally:
        await client.disconnect()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nSustabdyta.")
