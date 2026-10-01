"""Offline queue/worker checks; never contacts Telegram."""

import asyncio
import os
import sqlite3
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "offline")
os.environ.setdefault("BOT_TOKEN", "123456:offline")

import ads_builder as ab
import forwarder as fw


def test_queue(db):
    ab.init(db)
    assert ab.render_rose_body("Caption", [{"label": "Shop", "url": "https://example.com/a"}]) == \
        "Caption\n\n[Shop](buttonurl://https://example.com/a)"
    for bad_url in ("javascript:alert(1)", "https://a.example/path)bad", "https://a.example/a b"):
        try:
            ab.validate_button("Shop", bad_url)
        except ValueError:
            pass
        else:
            raise AssertionError("Invalid URL was accepted")
    try:
        ab.validate_size("photo", "🐉" * 600, [], "promo")
    except ValueError:
        pass
    else:
        raise AssertionError("UTF-16 caption limit was not enforced")
    first = ab.enqueue(db, "konkursas", 42, 100, "photo", "caption", True)
    assert first == 1
    row = ab.claim(db)
    assert row["status"] == "queued" and row["launch_now"] == 1
    assert ab.claim(db) is None
    ab.finish(db, first, "verified")
    assert ab.latest(db, 42)["status"] == "verified"
    replacement = ab.enqueue(db, "konkursas", 42, 101, "text", "Replacement", False)
    assert ab.claim(db)["id"] == replacement
    ab.finish(db, replacement, "failed", "Rose did not confirm")
    assert ab.active_ads(db)[0]["id"] == first
    second = ab.enqueue(db, "promo", 42, 101, "text", "Hello", False)
    assert ab.claim(db)["id"] == second
    ab.recover_after_worker_restart(db)
    assert ab.latest(db, 42)["status"] == "uncertain"


def test_migration(db):
    with sqlite3.connect(db) as conn:
        conn.execute("""CREATE TABLE ads_builder_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,note TEXT,admin_id INTEGER,
            source_message_id INTEGER,kind TEXT,body TEXT,launch_now INTEGER,
            status TEXT,error TEXT,created_at REAL,updated_at REAL)""")
        conn.execute("""INSERT INTO ads_builder_jobs
            (note,admin_id,source_message_id,kind,body,launch_now,status,error,created_at,updated_at)
            VALUES ('legacy',42,100,'text','Old ad',0,'verified','',1,1)""")
    ab.init(db)
    row = ab.active_ads(db)[0]
    assert row["note"] == "legacy" and row["buttons_json"] == "[]"
    assert row["operation"] == "save"


class FakeMessage:
    def __init__(self, mid, text="", *, sender=None, reply=None, media=None, buttons=None, reply_markup=None):
        self.id = mid
        self.raw_text = text
        self.reply_to_msg_id = reply
        self.media = media
        self.buttons = buttons
        self.reply_markup = reply_markup
        self.sender = sender

    async def get_sender(self):
        return self.sender


class FakeClient:
    def __init__(self, rose_text, media=None, buttons=None):
        self.counter = 10
        self.messages = []
        self.rose_text = rose_text
        self.media = media
        self.buttons = buttons
        self.deleted = []

    async def send_message(self, chat, text, reply_to=None):
        self.counter += 1
        command = FakeMessage(self.counter, text, reply=reply_to)
        self.messages.append(command)
        if text.startswith("/get"):
            self.counter += 1
            self.messages.append(FakeMessage(
                self.counter, self.rose_text,
                sender=SimpleNamespace(username="MissRose_bot"),
                reply=command.id, media=self.media, buttons=self.buttons,
            ))
        return command

    async def get_messages(self, chat, *, limit=None, ids=None):
        if ids is not None:
            return next((m for m in self.messages if m.id == ids), None)
        return list(reversed(self.messages[-limit:]))

    async def delete_messages(self, chat, ids, revoke=True):
        self.deleted.extend(ids)


async def test_worker(db):
    fw.DB_PATH = db
    buttons = [{"label": "Open", "url": "https://example.com"}]
    job_id = ab.enqueue(db, "promo", 42, 100, "photo", "My caption", True, buttons=buttons)
    job = ab.claim(db)
    wrapped = SimpleNamespace(text="Open", button=SimpleNamespace(url="https://example.com"))
    client = FakeClient("My caption", media=object(), buttons=[[wrapped]])
    bot = SimpleNamespace(
        get_chat=AsyncMock(return_value=SimpleNamespace(id=777)),
        get_chat_member=AsyncMock(return_value=SimpleNamespace(status="administrator")),
        copy_message=AsyncMock(return_value=SimpleNamespace(message_id=9)),
        send_message=AsyncMock(),
    )
    with patch.object(fw.asyncio, "sleep", new_callable=AsyncMock):
        await fw.process_ads_builder_job(client, bot, job)
    assert ab.latest(db, 42)["status"] == "verified"
    assert ab.active_ads(db)[0]["note"] == "promo"
    assert "[Open](buttonurl://https://example.com)" in bot.copy_message.await_args.kwargs["caption"]
    assert any(message.raw_text.startswith("/save@MissRose_bot promo") for message in client.messages)
    assert 9 in client.deleted
    assert max(m.id for m in client.messages) not in client.deleted  # Launched ad remains.
    bot.send_message.assert_awaited_once()


def test_raw_rose_markup():
    raw_button = SimpleNamespace(text="Open", type=SimpleNamespace(url="https://example.com"))
    message = FakeMessage(
        1, buttons=None,
        reply_markup=SimpleNamespace(rows=[SimpleNamespace(buttons=[raw_button])]),
    )
    assert fw.rose_reply_buttons(message) == {("Open", "https://example.com")}
    wrapped = SimpleNamespace(text="Open", url="https://example.com", button=raw_button)
    message.buttons = [[wrapped]]
    assert fw.rose_reply_buttons(message) == {("Open", "https://example.com")}


async def test_reconcile_wrong_buttons(db):
    fw.DB_PATH = db
    fw.init_settings()
    button = {"label": "Open", "url": "https://example.com"}
    job_id = ab.enqueue(db, "konkursas", 42, 100, "photo", "My caption", False, buttons=[button])
    assert ab.claim(db)["id"] == job_id
    ab.finish(db, job_id, "uncertain", "Rose atsakymas nepatvirtintas (wrong_buttons).")
    raw = SimpleNamespace(text="Open", type=SimpleNamespace(url="https://example.com"))
    wrapped = SimpleNamespace(text="Open", url="https://example.com", button=raw)
    client = FakeClient("My caption", media=object(), buttons=[[wrapped]])
    with patch.object(fw.asyncio, "sleep", new_callable=AsyncMock):
        await fw.reconcile_uncertain_rose_buttons(client)
    assert ab.active_ads(db)[0]["id"] == job_id
    assert fw.get_setting("ads_builder_reconcile_v2_once") == "1"
    count = len(client.messages)
    await fw.reconcile_uncertain_rose_buttons(client)
    assert len(client.messages) == count


async def test_missing_rose(db):
    fw.DB_PATH = db
    ab.enqueue(db, "konkursas", 42, 100, "text", "One ad", False)
    job = ab.claim(db)
    client = FakeClient("Note not found.")
    bot = SimpleNamespace(
        get_chat=AsyncMock(return_value=SimpleNamespace(id=777)),
        get_chat_member=AsyncMock(return_value=SimpleNamespace(status="administrator")),
        send_message=AsyncMock(),
    )
    with patch.object(fw.asyncio, "sleep", new_callable=AsyncMock):
        await fw.process_ads_builder_job(client, bot, job)
    assert ab.latest(db, 42)["status"] == "failed"
    assert max(m.id for m in client.messages) in client.deleted


async def test_delete(db):
    fw.DB_PATH = db
    fw.init_settings()
    old_id = ab.enqueue(db, "promo", 42, 100, "text", "My caption", False)
    assert ab.claim(db)["id"] == old_id
    ab.finish(db, old_id, "verified")
    fw.set_setting("ads_notes", '["promo"]')
    job_id = ab.enqueue_delete(db, "promo", 42)
    job = ab.claim(db)
    assert job["id"] == job_id
    client = FakeClient("Note not found.")
    bot = SimpleNamespace(
        get_chat=AsyncMock(return_value=SimpleNamespace(id=777)),
        get_chat_member=AsyncMock(return_value=SimpleNamespace(status="administrator")),
        send_message=AsyncMock(),
    )
    with patch.object(fw.asyncio, "sleep", new_callable=AsyncMock):
        await fw.process_ads_builder_job(client, bot, job)
    assert ab.latest(db, 42)["status"] == "deleted"
    assert any(message.raw_text == "/clear@MissRose_bot promo" for message in client.messages)
    assert ab.active_ads(db) == []
    assert fw.get_setting("ads_notes") == "[]"


with tempfile.TemporaryDirectory() as td:
    db = str(Path(td) / "ads.sqlite3")
    test_queue(db)
with tempfile.TemporaryDirectory() as td:
    test_migration(str(Path(td) / "legacy.sqlite3"))
with tempfile.TemporaryDirectory() as td:
    db = str(Path(td) / "ads.sqlite3")
    ab.init(db)
    asyncio.run(test_worker(db))
test_raw_rose_markup()
with tempfile.TemporaryDirectory() as td:
    db = str(Path(td) / "ads.sqlite3")
    ab.init(db)
    asyncio.run(test_reconcile_wrong_buttons(db))
with tempfile.TemporaryDirectory() as td:
    db = str(Path(td) / "ads.sqlite3")
    ab.init(db)
    asyncio.run(test_missing_rose(db))
with tempfile.TemporaryDirectory() as td:
    db = str(Path(td) / "ads.sqlite3")
    ab.init(db)
    asyncio.run(test_delete(db))
print("ADS builder offline tests passed")
