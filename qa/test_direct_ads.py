"""Offline, production-shaped native ADS scheduler and transport checks."""

import asyncio
import inspect
import json
import os
import sqlite3
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "offline")
os.environ.setdefault("BOT_TOKEN", "123456:offline")

import ads_builder as ab
import forwarder as fw

main_source = inspect.getsource(fw.main)
assert "direct_ads_loop()" in main_source
assert "rose_ads_loop(" not in main_source and "ads_builder_loop(" not in main_source


def fake_bot():
    result = SimpleNamespace(message_id=77)
    return SimpleNamespace(
        send_message=AsyncMock(return_value=result),
        send_photo=AsyncMock(return_value=result),
        send_video=AsyncMock(return_value=result),
        copy_message=AsyncMock(return_value=result),
    )


async def check(db):
    fw.DB_PATH = db
    fw.init_settings()
    ab.init(db)
    assert ab.validate_direct_size("photo", "x" * 1024, [{"label": "Open", "url": "https://example.com"}]) == "x" * 1024
    try:
        ab.validate_direct_size("photo", "x" * 1025, [])
    except ValueError:
        pass
    else:
        raise AssertionError("Oversized native caption accepted")
    bot = fake_bot()
    assert await fw.direct_ads_tick(bot) == "off"
    fw.set_setting("ads_enabled", "1")
    assert await fw.direct_ads_tick(bot) == "empty"
    assert fw.get_setting("ads_enabled") == "0"
    bot.send_message.assert_not_awaited()

    text_id = ab.save_direct(db, "promo", 42, 10, "text", "Plain caption",
                             buttons=[{"label": "Shop", "url": "https://example.com"}])
    assert ab.active_by_note(db, "promo")["id"] == text_id
    fw.set_setting("ads_notes", json.dumps(["promo"]))
    fw.set_setting("ads_enabled", "1")
    assert await fw.direct_ads_tick(bot) == "waiting"
    fw.set_setting("ads_fast_last_sent_ts", str(time.time() - 1200))
    assert await fw.direct_ads_tick(bot) == "sent"
    sent = bot.send_message.await_args.kwargs
    assert sent["chat_id"] == fw.ROSE_ADS_CHAT
    assert sent["text"] == "Plain caption" and sent["disable_notification"] is True
    assert sent["reply_markup"].inline_keyboard[0][0].url == "https://example.com"
    assert fw.get_setting("ads_last_result") == "ok"
    assert await fw.direct_ads_tick(bot) == "gap"

    photo_id = ab.save_direct(db, "photo", 42, 11, "photo", "Photo caption", media_file_id="bot-file-id")
    fw.set_setting("ads_enabled", "0")
    fw.set_setting("ads_force_job_id", str(photo_id))
    assert await fw.direct_ads_tick(bot) == "sent"
    assert bot.send_photo.await_args.kwargs["photo"] == "bot-file-id"
    assert fw.get_setting("ads_force_job_id") == "0"
    assert await fw.direct_ads_tick(bot) == "off"
    assert bot.send_photo.await_count == 1

    old_id = ab.save_direct(db, "old", 42, 12, "photo", "Old caption", source_chat_id=42)
    fw.set_setting("ads_force_job_id", str(old_id))
    assert await fw.direct_ads_tick(bot) == "sent"
    copied = bot.copy_message.await_args.kwargs
    assert copied["from_chat_id"] == 42 and copied["message_id"] == 12
    assert copied["disable_notification"] is True

    ab.delete_direct(db, "old", 42)
    assert ab.active_by_note(db, "old") is None
    fw.set_setting("ads_force_job_id", str(old_id))
    assert await fw.direct_ads_tick(bot) == "skipped"
    assert bot.copy_message.await_count == 1

    broken_id = ab.save_direct(db, "broken", 42, 13, "photo", "Missing source", source_chat_id=42)
    bot.copy_message.side_effect = RuntimeError("source unavailable")
    fw.set_setting("ads_force_job_id", str(broken_id))
    fw.set_setting("ads_enabled", "1")
    assert await fw.direct_ads_tick(bot) == "failed"
    assert fw.get_setting("ads_enabled") == "0"
    assert fw.get_setting("ads_last_result") == "error"


with tempfile.TemporaryDirectory() as td:
    asyncio.run(check(str(Path(td) / "native.sqlite3")))
print("Native ADS offline tests passed")
