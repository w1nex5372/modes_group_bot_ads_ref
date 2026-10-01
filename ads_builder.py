"""Durable admin-to-Rose ad draft queue shared by the bot and user worker."""

import re
import json
import sqlite3
import time
from contextlib import closing
from urllib.parse import urlsplit


NAME_RE = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
BUTTON_LABEL_RE = re.compile(r"[^\[\]()\r\n]{1,40}\Z")


def connect(db_path):
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init(db_path):
    with closing(connect(db_path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("""CREATE TABLE IF NOT EXISTS ads_builder_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            note TEXT NOT NULL,
            admin_id INTEGER NOT NULL,
            source_message_id INTEGER NOT NULL,
            kind TEXT NOT NULL CHECK(kind IN ('text','photo','video')),
            body TEXT NOT NULL,
            launch_now INTEGER NOT NULL CHECK(launch_now IN (0,1)),
            status TEXT NOT NULL,
            error TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        )""")
        existing = {row[1] for row in conn.execute("PRAGMA table_info(ads_builder_jobs)")}
        for column, definition in (
            ("buttons_json", "TEXT NOT NULL DEFAULT '[]'"),
            ("source_chat_id", "INTEGER NOT NULL DEFAULT 0"),
            ("operation", "TEXT NOT NULL DEFAULT 'save'"),
            ("media_file_id", "TEXT NOT NULL DEFAULT ''"),
            ("entities_json", "TEXT NOT NULL DEFAULT '[]'"),
        ):
            if column not in existing:
                conn.execute(f"ALTER TABLE ads_builder_jobs ADD COLUMN {column} {definition}")
        conn.commit()


def validate_button(label, url, icon_custom_emoji_id=""):
    label = str(label).strip()
    url = str(url).strip()
    if not BUTTON_LABEL_RE.fullmatch(label) or any(ord(c) < 32 or ord(c) == 127 for c in label):
        raise ValueError("Button label must be 1-40 characters without brackets")
    if len(url) > 500 or any(c.isspace() or ord(c) < 32 or c in "()[]\\<>\"'" for c in url):
        raise ValueError("Invalid button URL")
    parsed = urlsplit(url)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Button URL must be an HTTP(S) link")
    button = {"label": label, "url": url}
    if icon_custom_emoji_id:
        icon = str(icon_custom_emoji_id).strip()
        if not icon.isascii() or not icon.isdecimal() or len(icon) > 25:
            raise ValueError("Invalid custom emoji ID")
        button["icon_custom_emoji_id"] = icon
    return button


def normalize_buttons(buttons):
    if not isinstance(buttons, list) or len(buttons) > 6:
        raise ValueError("Maximum 6 buttons")
    return [validate_button(item["label"], item["url"], item.get("icon_custom_emoji_id", ""))
            for item in buttons]


def normalize_entities(entities, body):
    """Keep Telegram UTF-16 entity offsets and reject malformed stored formatting."""
    if not isinstance(entities, (list, tuple)) or len(entities) > 100:
        raise ValueError("Invalid ad entities")
    length = len(body.encode("utf-16-le")) // 2
    result = []
    for value in entities:
        if not isinstance(value, dict):
            raise ValueError("Invalid ad entity")
        item = dict(value)
        if (not isinstance(item.get("type"), str)
                or type(item.get("offset")) is not int
                or type(item.get("length")) is not int
                or item["offset"] < 0 or item["length"] < 1
                or item["offset"] + item["length"] > length):
            raise ValueError("Invalid ad entity offset")
        if item["type"] == "custom_emoji" and not str(item.get("custom_emoji_id", "")).isdecimal():
            raise ValueError("Invalid custom emoji entity")
        result.append(item)
    return result


def message_entities(entities_json):
    """Rehydrate the Bot API entities stored with an ad."""
    from telegram import MessageEntity

    return [MessageEntity.de_json(item) for item in json.loads(entities_json or "[]")]


def render_rose_body(body, buttons):
    buttons = normalize_buttons(buttons)
    suffix = "\n".join(f"[{b['label']}](buttonurl://{b['url']})" for b in buttons)
    return body + ("\n\n" + suffix if suffix else "")


def validate_size(kind, body, buttons, note=""):
    rendered = render_rose_body(body, buttons)
    limit = 1000 if kind in {"photo", "video"} else 4000 - len(note)
    units = len(rendered.encode("utf-16-le")) // 2
    if not body or units > limit:
        raise ValueError(f"Reklama su mygtukais per ilga ({units}/{limit})")
    return rendered


def validate_direct_size(kind, body, buttons):
    """Bot API buttons are separate markup, not part of the text/caption."""
    normalize_buttons(buttons)
    limit = 1024 if kind in {"photo", "video"} else 4096
    units = len(body.encode("utf-16-le")) // 2 if isinstance(body, str) else 0
    if kind not in {"text", "photo", "video"} or not body or units > limit:
        raise ValueError(f"Reklamos tekstas per ilgas arba tuščias ({units}/{limit})")
    return body


def enqueue(db_path, note, admin_id, source_message_id, kind, body, launch_now,
            buttons=None, source_chat_id=None):
    if not NAME_RE.fullmatch(note):
        raise ValueError("Invalid Rose note name")
    if kind not in {"text", "photo", "video"}:
        raise ValueError("Invalid ad kind")
    buttons = normalize_buttons(buttons or [])
    validate_size(kind, body, buttons, note)
    now = time.time()
    with closing(connect(db_path)) as conn:
        cursor = conn.execute(
            """INSERT INTO ads_builder_jobs
            (note,admin_id,source_message_id,kind,body,launch_now,status,created_at,updated_at,
             buttons_json,source_chat_id,operation)
            VALUES (?,?,?,?,?,?, 'queued',?,?,?,?,'save')""",
            (note, admin_id, source_message_id, kind, body, int(launch_now), now, now,
             json.dumps(buttons, ensure_ascii=False), source_chat_id or admin_id),
        )
        conn.commit()
        return cursor.lastrowid


def enqueue_delete(db_path, note, admin_id):
    if not NAME_RE.fullmatch(note):
        raise ValueError("Invalid Rose note name")
    now = time.time()
    with closing(connect(db_path)) as conn:
        cursor = conn.execute(
            """INSERT INTO ads_builder_jobs
            (note,admin_id,source_message_id,kind,body,launch_now,status,created_at,updated_at,
             buttons_json,source_chat_id,operation)
            VALUES (?,?,0,'text','',0,'queued',? ,?,'[]',0,'delete')""",
            (note, admin_id, now, now),
        )
        conn.commit()
        return cursor.lastrowid


def save_direct(db_path, note, admin_id, source_message_id, kind, body,
                buttons=None, source_chat_id=None, media_file_id="", entities=None):
    """Persist an ad locally; no Rose command or group send is required."""
    if not NAME_RE.fullmatch(note) or kind not in {"text", "photo", "video"}:
        raise ValueError("Invalid ad name or kind")
    buttons = normalize_buttons(buttons or [])
    validate_direct_size(kind, body, buttons)
    entities = normalize_entities(entities or [], body)
    if kind in {"photo", "video"} and not (media_file_id or source_message_id):
        raise ValueError("Media reference missing")
    now = time.time()
    with closing(connect(db_path)) as conn:
        cursor = conn.execute(
            """INSERT INTO ads_builder_jobs
            (note,admin_id,source_message_id,kind,body,launch_now,status,error,
             created_at,updated_at,buttons_json,source_chat_id,operation,media_file_id,entities_json)
            VALUES (?,?,?,?,?,0,'verified','',?,?,?,?, 'save',?,?)""",
            (note, admin_id, source_message_id or 0, kind, body, now, now,
             json.dumps(buttons, ensure_ascii=False), source_chat_id or admin_id,
             media_file_id or "", json.dumps(entities, ensure_ascii=False)),
        )
        conn.commit()
        return cursor.lastrowid


def delete_direct(db_path, note, admin_id):
    """Tombstone a managed ad locally without invoking Rose."""
    if not NAME_RE.fullmatch(note):
        raise ValueError("Invalid ad name")
    now = time.time()
    with closing(connect(db_path)) as conn:
        cursor = conn.execute(
            """INSERT INTO ads_builder_jobs
            (note,admin_id,source_message_id,kind,body,launch_now,status,error,
             created_at,updated_at,buttons_json,source_chat_id,operation,media_file_id)
            VALUES (?,?,0,'text','',0,'deleted','',?,?,'[]',0,'delete','')""",
            (note, admin_id, now, now),
        )
        conn.commit()
        return cursor.lastrowid


def claim(db_path):
    with closing(connect(db_path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM ads_builder_jobs WHERE status='queued' ORDER BY id LIMIT 1"
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE ads_builder_jobs SET status='processing',updated_at=? WHERE id=?",
                (time.time(), row["id"]),
            )
        conn.commit()
        return dict(row) if row else None


def finish(db_path, job_id, status, error=""):
    if status not in {"verified", "deleted", "failed", "uncertain"}:
        raise ValueError("Invalid job status")
    with closing(connect(db_path)) as conn:
        conn.execute(
            "UPDATE ads_builder_jobs SET status=?,error=?,updated_at=? WHERE id=? AND status='processing'",
            (status, error[:300], time.time(), job_id),
        )
        conn.commit()


def reconcile_verified(db_path, job_id):
    """Confirm only the latest old false-negative after an exact Rose readback."""
    with closing(connect(db_path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.execute(
            """UPDATE ads_builder_jobs SET status='verified',error='',updated_at=?
               WHERE id=? AND status='uncertain' AND operation='save'
               AND error LIKE '%wrong_buttons%'
               AND NOT EXISTS (SELECT 1 FROM ads_builder_jobs later
                   WHERE later.note=ads_builder_jobs.note AND later.id>ads_builder_jobs.id)""",
            (time.time(), job_id),
        )
        conn.commit()
        return cursor.rowcount == 1


def latest(db_path, admin_id):
    with closing(connect(db_path)) as conn:
        row = conn.execute(
            "SELECT * FROM ads_builder_jobs WHERE admin_id=? ORDER BY id DESC LIMIT 1",
            (admin_id,),
        ).fetchone()
        return dict(row) if row else None


def active_ads(db_path):
    """Latest successful operation per note; failed edits do not hide old ads."""
    with closing(connect(db_path)) as conn:
        rows = conn.execute("""SELECT j.* FROM ads_builder_jobs j
            JOIN (SELECT note, MAX(id) id FROM ads_builder_jobs
                  WHERE status IN ('verified','deleted') GROUP BY note) current
              ON current.id=j.id
            WHERE j.status='verified' ORDER BY j.id DESC""").fetchall()
        return [dict(row) for row in rows]


def active_by_id(db_path, job_id):
    return next((row for row in active_ads(db_path) if row["id"] == job_id), None)


def active_by_note(db_path, note):
    return next((row for row in active_ads(db_path) if row["note"] == note), None)


def recover_after_worker_restart(db_path):
    # A crashed worker may have saved the note before persisting the outcome.
    # Never replay an ambiguous public operation automatically.
    with closing(connect(db_path)) as conn:
        conn.execute(
            """UPDATE ads_builder_jobs SET status='uncertain',
            error='Darbuotojas nutrūko. Patikrink Rose rankiniu būdu; automatiškai nekartosiu.',
            updated_at=? WHERE status='processing'""",
            (time.time(),),
        )
        conn.commit()
