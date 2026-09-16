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
SOURCE_CHAT_RAW = os.getenv("SOURCE_CHAT", MAIN_GROUP).strip()
SOURCE_MESSAGE_ID = int(os.getenv("SOURCE_MESSAGE_ID", "0") or "0")
INTERVAL_SECONDS = int(os.getenv("INTERVAL_SECONDS", "3600"))
PER_CHAT_DELAY_SECONDS = int(os.getenv("PER_CHAT_DELAY_SECONDS", "5"))
CHATS_FILE = os.getenv("CHATS_FILE", "chats.txt").strip()

# Rose ADS automatizacija pagrindinėje grupėje.
ROSE_ADS_CHAT_RAW = os.getenv("ROSE_ADS_CHAT", MAIN_GROUP).strip()
ROSE_BOT_USERNAME = os.getenv("ROSE_BOT_USERNAME", "").strip().lstrip("@")
ROSE_COMMAND_DELETE_DELAY_SECONDS = int(
    os.getenv("ROSE_COMMAND_DELETE_DELAY_SECONDS", "3")
)

# FAST eilė: šie Rose note vardai leidžiami pakaitomis kas 15 min.
ROSE_FAST_NOTES = [
    item.strip()
    for item in os.getenv("ROSE_FAST_NOTES", "konkursas,promo").split(",")
    if item.strip()
]
ROSE_FAST_INTERVAL_MINUTES = max(
    5, int(os.getenv("ROSE_FAST_INTERVAL_MINUTES", "15"))
)

# NORMAL eilė: visi kiti /adsset note vardai. Intervalą valdo /adsinterval.
# Apsauga, kad du ADS neatsirastų beveik tuo pačiu metu.
ROSE_MIN_AD_GAP_SECONDS = max(
    10, int(os.getenv("ROSE_MIN_AD_GAP_SECONDS", "120"))
)

# Tas pats DB, kurį naudoja referral botas.
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
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        defaults = {
            "ads_enabled": "0",
            "ads_interval_minutes": "30",
            "ads_notes": json.dumps(["ads"], ensure_ascii=False),
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
            conn.execute(
                "INSERT OR IGNORE INTO settings(key, value) VALUES(?, ?)",
                (key, value),
            )
        conn.commit()


def get_setting(key: str, default=None):
    with closing(settings_db()) as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key=?",
            (key,),
        ).fetchone()
        return row["value"] if row else default


def set_setting(key: str, value):
    with closing(settings_db()) as conn:
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
        return ["ads"]

    if not isinstance(notes, list):
        return ["ads"]

    result = []
    for note in notes:
        note = str(note).strip()
        if note and note not in result:
            result.append(note)
    return result


def split_ads_notes():
    notes = get_ads_notes()
    fast_names = set(ROSE_FAST_NOTES)
    fast = [note for note in notes if note in fast_names]
    normal = [note for note in notes if note not in fast_names]
    return fast, normal


def safe_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def local_time_string(ts=None):
    ts = ts or time.time()
    return datetime.fromtimestamp(ts).strftime("%H:%M:%S")


def load_targets():
    path = Path(CHATS_FILE)
    if not path.exists():
        raise FileNotFoundError(f"Nerastas {CHATS_FILE}")

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
        key = str(peer)

        if key not in seen:
            seen.add(key)
            targets.append(peer)

    return targets


async def forward_round(client: TelegramClient):
    if SOURCE_MESSAGE_ID <= 0:
        print("Auto-forward praleistas: SOURCE_MESSAGE_ID=0.")
        return

    targets = load_targets()
    if not targets:
        print("Auto-forward praleistas: chats.txt nėra tikslinių grupių.")
        return

    print(f"\n--- Naujas reklamos forward ciklas: {len(targets)} grupių ---")

    for index, target in enumerate(targets, start=1):
        try:
            await client.forward_messages(
                entity=target,
                messages=SOURCE_MESSAGE_ID,
                from_peer=SOURCE_CHAT,
            )
            print(f"[{index}/{len(targets)}] OK -> {target}")

        except errors.FloodWaitError as e:
            wait_for = e.seconds + 5
            print(
                f"[{index}/{len(targets)}] FLOOD_WAIT {e.seconds}s "
                f"-> laukiu {wait_for}s"
            )
            await asyncio.sleep(wait_for)

        except errors.ChatWriteForbiddenError:
            print(f"[{index}/{len(targets)}] NEGALIMA RAŠYTI -> {target}")

        except errors.UserBannedInChannelError:
            print(
                f"[{index}/{len(targets)}] ACCOUNT APRIBOTAS / "
                f"UŽBANINTAS -> {target}"
            )

        except errors.SlowModeWaitError as e:
            print(
                f"[{index}/{len(targets)}] SLOW MODE {e.seconds}s "
                f"-> {target} (praleista)"
            )

        except Exception as e:
            print(
                f"[{index}/{len(targets)}] KLAIDA -> {target}: "
                f"{type(e).__name__}: {e}"
            )

        if index < len(targets):
            await asyncio.sleep(PER_CHAT_DELAY_SECONDS)


async def forward_loop(client: TelegramClient):
    while True:
        try:
            await forward_round(client)
        except Exception as e:
            print(f"Forward ciklo klaida: {type(e).__name__}: {e}")

        print(
            f"Forward ciklas baigtas. Kitas po "
            f"{INTERVAL_SECONDS // 60} min."
        )
        await asyncio.sleep(INTERVAL_SECONDS)


def rose_get_command(note: str):
    if ROSE_BOT_USERNAME:
        return f"/get@{ROSE_BOT_USERNAME} {note}"
    return f"/get {note}"


async def verify_rose_response(client: TelegramClient, sent_message_id: int):
    """
    Bando patvirtinti, kad Rose realiai atsakė į mūsų /get komandą.
    Patikimiausias signalas: naujesnė boto žinutė, kuri reply'ina į komandą.
    """
    try:
        messages = await client.get_messages(ROSE_ADS_CHAT, limit=12)
        target_username = ROSE_BOT_USERNAME.lower()

        for message in messages:
            if not message or message.id <= sent_message_id:
                continue

            reply_to_id = getattr(message, "reply_to_msg_id", None)
            sender = await message.get_sender()
            sender_is_bot = bool(getattr(sender, "bot", False))
            sender_username = (getattr(sender, "username", "") or "").lower()

            if reply_to_id == sent_message_id and sender_is_bot:
                return True, message.id

            if (
                target_username
                and sender_is_bot
                and sender_username == target_username
            ):
                return True, message.id

        return False, None
    except Exception:
        return False, None


def record_ads_result(note, lane, result, verified=False, error="", response_id=None):
    now = time.time()
    set_setting("ads_last_sent_ts", str(now))
    set_setting("ads_last_note", note)
    set_setting("ads_last_lane", lane)
    set_setting("ads_last_result", result)
    set_setting("ads_last_verified", "1" if verified else "0")
    set_setting("ads_last_error", error or "")
    set_setting("ads_last_response_id", response_id or "")


async def send_rose_note(client: TelegramClient, note: str, lane: str):
    command = rose_get_command(note)

    try:
        sent = await client.send_message(ROSE_ADS_CHAT, command)

        await asyncio.sleep(max(1, ROSE_COMMAND_DELETE_DELAY_SECONDS))

        verified, response_id = await verify_rose_response(client, sent.id)

        try:
            await client.delete_messages(
                ROSE_ADS_CHAT,
                [sent.id],
                revoke=True,
            )
        except Exception as e:
            print(
                f"Rose ADS: nepavyko ištrinti /get komandos: "
                f"{type(e).__name__}: {e}"
            )

        now = time.time()
        if lane == "fast":
            set_setting("ads_fast_last_sent_ts", str(now))
        elif lane == "normal":
            set_setting("ads_normal_last_sent_ts", str(now))

        record_ads_result(
            note=note,
            lane=lane,
            result="ok" if verified else "command_sent_unverified",
            verified=verified,
            response_id=response_id,
        )

        marker = "✅ VERIFIED" if verified else "⚠️ NEPATVIRTINTA"
        print(
            f"[{local_time_string(now)}] Rose ADS {marker} | "
            f"{lane.upper()} | note={note}"
            + (f" | Rose message={response_id}" if response_id else "")
        )
        return True

    except Exception as e:
        error_text = f"{type(e).__name__}: {e}"
        record_ads_result(
            note=note,
            lane=lane,
            result="error",
            verified=False,
            error=error_text,
        )
        print(
            f"[{local_time_string()}] Rose ADS KLAIDA ❌ | "
            f"{lane.upper()} | note={note} | {error_text}"
        )
        raise


def next_lane_note(lane: str):
    fast, normal = split_ads_notes()
    notes = fast if lane == "fast" else normal

    if not notes:
        return None

    key = "ads_fast_index" if lane == "fast" else "ads_normal_index"
    idx = safe_int(get_setting(key, "0"), 0)
    note = notes[idx % len(notes)]
    set_setting(key, str((idx + 1) % len(notes)))
    return note


def next_force_note():
    notes = get_ads_notes()
    if not notes:
        return None, None

    idx = safe_int(get_setting("ads_next_index", "0"), 0)
    note = notes[idx % len(notes)]
    set_setting("ads_next_index", str((idx + 1) % len(notes)))

    lane = "fast" if note in set(ROSE_FAST_NOTES) else "normal"
    return note, lane


def initialize_scheduler_if_needed():
    if get_setting("ads_scheduler_initialized", "0") == "1":
        return

    now = time.time()
    set_setting("ads_fast_last_sent_ts", str(now))
    set_setting("ads_normal_last_sent_ts", str(now))
    set_setting("ads_scheduler_initialized", "1")

    print(
        f"[{local_time_string(now)}] Rose ADS scheduler init | "
        f"FAST kas {ROSE_FAST_INTERVAL_MINUTES} min: "
        f"{', '.join(ROSE_FAST_NOTES) or '—'} | "
        f"NORMAL intervalą valdo /adsinterval"
    )


async def rose_ads_loop(client: TelegramClient):
    print("Rose ADS rotacijos valdymas paruoštas.")
    print(
        f"FAST notes kas {ROSE_FAST_INTERVAL_MINUTES} min: "
        f"{', '.join(ROSE_FAST_NOTES) or '—'}"
    )
    print("Kiti notes naudoja /adsinterval reikšmę (pvz. 30 min).")
    print("Patikrinimas: run_ads_status.bat arba terminalo VERIFIED eilutės.")

    while True:
        try:
            enabled = get_setting("ads_enabled", "0") == "1"

            if not enabled:
                await asyncio.sleep(5)
                continue

            initialize_scheduler_if_needed()

            normal_interval_minutes = safe_int(
                get_setting("ads_interval_minutes", "30"),
                30,
            )
            normal_interval_minutes = max(5, min(1440, normal_interval_minutes))

            fast_notes, normal_notes = split_ads_notes()
            now = time.time()

            last_any = safe_float(get_setting("ads_last_sent_ts", "0"), 0.0)
            last_fast = safe_float(
                get_setting("ads_fast_last_sent_ts", "0"),
                0.0,
            )
            last_normal = safe_float(
                get_setting("ads_normal_last_sent_ts", "0"),
                0.0,
            )

            force_send = get_setting("ads_force_send", "0") == "1"

            if force_send:
                note, lane = next_force_note()
                set_setting("ads_force_send", "0")

                if note:
                    await send_rose_note(client, note, lane)
                else:
                    print("Rose ADS: /adsset sąrašas tuščias.")

                await asyncio.sleep(5)
                continue

            # Neleidžiame dviem reklamos postams sukristi beveik vienu metu.
            if last_any and (now - last_any) < ROSE_MIN_AD_GAP_SECONDS:
                await asyncio.sleep(5)
                continue

            fast_due = (
                bool(fast_notes)
                and (now - last_fast) >= ROSE_FAST_INTERVAL_MINUTES * 60
            )
            normal_due = (
                bool(normal_notes)
                and (now - last_normal) >= normal_interval_minutes * 60
            )

            if fast_due and normal_due:
                fast_overdue = (
                    now - last_fast
                ) - ROSE_FAST_INTERVAL_MINUTES * 60
                normal_overdue = (
                    now - last_normal
                ) - normal_interval_minutes * 60

                lane = "fast" if fast_overdue >= normal_overdue else "normal"
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
            set_setting(
                "ads_last_error",
                f"FloodWaitError: {e.seconds}s",
            )
            print(f"Rose ADS FLOOD_WAIT: laukiu {e.seconds + 5}s")
            await asyncio.sleep(e.seconds + 5)

        except errors.ChatWriteForbiddenError:
            set_setting(
                "ads_last_error",
                f"ChatWriteForbiddenError: {ROSE_ADS_CHAT}",
            )
            print(
                f"Rose ADS: accountas negali rašyti į {ROSE_ADS_CHAT}."
            )

        except Exception as e:
            set_setting(
                "ads_last_error",
                f"{type(e).__name__}: {e}",
            )
            print(
                f"Rose ADS klaida: {type(e).__name__}: {e}"
            )

        await asyncio.sleep(5)


async def main():
    init_settings()

    client = TelegramClient(
        SESSION_NAME,
        API_ID,
        API_HASH,
    )

    print("Jungiamasi prie Telegram user automation...")
    await client.start()

    me = await client.get_me()

    print(
        f"Prisijungta kaip: {me.first_name or ''} "
        f"(@{me.username or 'be_username'})"
    )
    print(
        f"Auto-forward intervalas: kas "
        f"{INTERVAL_SECONDS // 60} min."
    )
    print(f"Rose ADS grupė: {ROSE_ADS_CHAT}")
    print("CTRL+C sustabdyti.\n")

    try:
        await asyncio.gather(
            forward_loop(client),
            rose_ads_loop(client),
        )
    finally:
        await client.disconnect()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nTelegram automatizacija sustabdyta.")
