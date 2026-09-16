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
            "ads_notes": json.dumps(["konkursas", "promo"], ensure_ascii=False),
            "ads_next_index": "0",
            "ads_fast_index": "0",
            "ads_normal_index": "0",
            "ads_last_sent_ts": "0",
            "ads_fast_last_sent_ts": "0",
            "ads_normal_last_sent_ts": "0",
            "ads_force_send": "0",
            "ads_last_note": "",
            "ads_last_lane": "",
            "ads_last_result": "never",
            "ads_last_verified": "0",
            "ads_last_error": "",
            "ads_last_response_id": "",
            "ads_scheduler_initialized": "0",
        }
        for key, value in defaults.items():
            conn.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (key, value))
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
            if getattr(msg, "reply_to_msg_id", None) == sent_id:
                return True, msg.id
            if wanted and username == wanted:
                return True, msg.id
        return False, None
    except Exception:
        return False, None


def record_result(note, lane, result, verified=False, error="", response_id=None):
    now = time.time()
    set_setting("ads_last_sent_ts", now)
    set_setting("ads_last_note", note)
    set_setting("ads_last_lane", lane)
    set_setting("ads_last_result", result)
    set_setting("ads_last_verified", "1" if verified else "0")
    set_setting("ads_last_error", error)
    set_setting("ads_last_response_id", response_id or "")


async def send_rose_note(client, note, lane):
    command = rose_get_command(note)
    try:
        sent = await client.send_message(ROSE_ADS_CHAT, command)
        await asyncio.sleep(max(1, ROSE_COMMAND_DELETE_DELAY_SECONDS))
        verified, response_id = await verify_rose_response(client, sent.id)
        try:
            await client.delete_messages(ROSE_ADS_CHAT, [sent.id], revoke=True)
        except Exception:
            pass
        now = time.time()
        if lane == "fast":
            set_setting("ads_fast_last_sent_ts", now)
        elif lane == "normal":
            set_setting("ads_normal_last_sent_ts", now)
        record_result(note, lane, "ok" if verified else "command_sent_unverified", verified, response_id=response_id)
        marker = "✅ VERIFIED" if verified else "⚠️ UNVERIFIED"
        print(f"[{stamp()}] Rose ADS {marker} | {lane.upper()} | {note}")
        return verified
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
        record_result(note, lane, "error", False, err)
        print(f"[{stamp()}] Rose ADS ❌ | {lane.upper()} | {note} | {err}")
        raise


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


async def rose_ads_loop(client):
    print(f"Rose ADS grupė: {ROSE_ADS_CHAT}")
    while True:
        try:
            if get_setting("ads_enabled", "0") != "1":
                await asyncio.sleep(5)
                continue
            initialize_scheduler()
            fast_interval = max(5, min(1440, safe_int(get_setting("ads_fast_interval_minutes", ROSE_FAST_INTERVAL_DEFAULT), ROSE_FAST_INTERVAL_DEFAULT)))
            normal_interval = max(5, min(1440, safe_int(get_setting("ads_interval_minutes", "30"), 30)))
            fast_notes, normal_notes = split_ads_notes()
            now = time.time()
            last_any = safe_float(get_setting("ads_last_sent_ts", "0"), 0)
            last_fast = safe_float(get_setting("ads_fast_last_sent_ts", "0"), 0)
            last_normal = safe_float(get_setting("ads_normal_last_sent_ts", "0"), 0)
            if get_setting("ads_force_send", "0") == "1":
                set_setting("ads_force_send", "0")
                note, lane = next_force_note()
                if note:
                    await send_rose_note(client, note, lane)
                await asyncio.sleep(5)
                continue
            if last_any and now - last_any < ROSE_MIN_AD_GAP_SECONDS:
                await asyncio.sleep(5)
                continue
            fast_due = bool(fast_notes) and now - last_fast >= fast_interval * 60
            normal_due = bool(normal_notes) and now - last_normal >= normal_interval * 60
            if fast_due and normal_due:
                fast_over = now - last_fast - fast_interval * 60
                normal_over = now - last_normal - normal_interval * 60
                lane = "fast" if fast_over >= normal_over else "normal"
                note = next_lane_note(lane)
                if note:
                    await send_rose_note(client, note, lane)
            elif fast_due:
                note = next_lane_note("fast")
                if note:
                    await send_rose_note(client, note, "fast")
            elif normal_due:
                note = next_lane_note("normal")
                if note:
                    await send_rose_note(client, note, "normal")
        except errors.FloodWaitError as e:
            set_setting("ads_last_error", f"FloodWaitError: {e.seconds}s")
            await asyncio.sleep(e.seconds + 5)
        except errors.ChatWriteForbiddenError:
            set_setting("ads_last_error", f"ChatWriteForbiddenError: {ROSE_ADS_CHAT}")
            print(f"Rose ADS: negalima rašyti į {ROSE_ADS_CHAT}")
        except Exception as e:
            set_setting("ads_last_error", f"{type(e).__name__}: {e}")
            print(f"Rose ADS klaida: {type(e).__name__}: {e}")
        await asyncio.sleep(5)


async def main():
    init_settings()
    client = TelegramClient(SESSION_NAME, API_ID, API_HASH)
    print("Jungiamasi prie Telegram user automation...")
    await client.start()
    me = await client.get_me()
    print(f"Prisijungta kaip: {me.first_name or ''} (@{me.username or 'be_username'})")
    print("FAST ADS intervalas valdomas /adsfast, NORMAL — /adsinterval")
    print("CTRL+C sustabdyti.\n")
    try:
        await asyncio.gather(forward_loop(client), rose_ads_loop(client))
    finally:
        await client.disconnect()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nSustabdyta.")
