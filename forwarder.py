import asyncio
import json
import os
import sqlite3
import time
from contextlib import closing
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

# Rose notes rotacija tavo pagrindinėje grupėje.
ROSE_ADS_CHAT_RAW = os.getenv("ROSE_ADS_CHAT", MAIN_GROUP).strip()
ROSE_BOT_USERNAME = os.getenv("ROSE_BOT_USERNAME", "").strip().lstrip("@")
ROSE_COMMAND_DELETE_DELAY_SECONDS = int(
    os.getenv("ROSE_COMMAND_DELETE_DELAY_SECONDS", "3")
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
            "ads_last_sent_ts": "0",
            "ads_force_send": "0",
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


async def send_next_rose_ad(client: TelegramClient):
    notes = get_ads_notes()
    if not notes:
        print("Rose ADS: nėra note vardų. Naudok /adsset bote.")
        return

    try:
        idx = int(get_setting("ads_next_index", "0"))
    except ValueError:
        idx = 0

    note = notes[idx % len(notes)]
    command = rose_get_command(note)

    sent = await client.send_message(ROSE_ADS_CHAT, command)
    print(f"Rose ADS -> paleista note: {note}")

    # Duodame Rose kelias sekundes komandai perskaityti,
    # tada savo /get komandą pašaliname iš grupės.
    await asyncio.sleep(max(1, ROSE_COMMAND_DELETE_DELAY_SECONDS))

    try:
        await client.delete_messages(ROSE_ADS_CHAT, [sent.id], revoke=True)
    except Exception as e:
        print(
            f"Rose ADS: nepavyko ištrinti /get komandos: "
            f"{type(e).__name__}: {e}"
        )

    set_setting("ads_next_index", str((idx + 1) % len(notes)))
    set_setting("ads_last_sent_ts", str(time.time()))
    set_setting("ads_force_send", "0")


async def rose_ads_loop(client: TelegramClient):
    print("Rose ADS rotacijos valdymas paruoštas.")
    print("Valdymas per referral botą: /ads")

    while True:
        try:
            enabled = get_setting("ads_enabled", "0") == "1"

            try:
                interval_minutes = int(
                    get_setting("ads_interval_minutes", "30")
                )
            except ValueError:
                interval_minutes = 30

            interval_minutes = max(5, min(1440, interval_minutes))

            try:
                last_sent = float(
                    get_setting("ads_last_sent_ts", "0")
                )
            except ValueError:
                last_sent = 0.0

            force_send = get_setting("ads_force_send", "0") == "1"
            due = (time.time() - last_sent) >= interval_minutes * 60

            if enabled and (force_send or due):
                await send_next_rose_ad(client)

        except errors.FloodWaitError as e:
            print(f"Rose ADS FLOOD_WAIT: laukiu {e.seconds + 5}s")
            await asyncio.sleep(e.seconds + 5)

        except errors.ChatWriteForbiddenError:
            print(
                f"Rose ADS: accountas negali rašyti į {ROSE_ADS_CHAT}."
            )

        except Exception as e:
            print(
                f"Rose ADS klaida: {type(e).__name__}: {e}"
            )

        # Tikriname dažnai, kad /adsinterval ar /adsnext sureaguotų greitai.
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
