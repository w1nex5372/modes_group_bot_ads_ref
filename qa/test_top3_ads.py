"""Offline TOP 3 render + real scheduler-tick path with fake Bot API."""

import asyncio
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
os.environ["BOT_USERNAME"] = "ERRORAS1_BOT"

import forwarder as fw
import top3_ads


async def check(db):
    fw.DB_PATH = db
    fw.init_settings()
    with sqlite3.connect(db) as conn:
        conn.execute("""CREATE TABLE users (
            user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, weekly_points INTEGER)""")
        conn.executemany(
            "INSERT INTO users VALUES (?,?,?,?)",
            [
                (1, "admin", "Admin", 100),
                (2, "alice", "Alice", 8),
                (3, None, "Bob <test>", 5),
                (4, "charlie", "Charlie", 3),
                (5, "fourth", "Fourth", 1),
            ],
        )
    bot = SimpleNamespace(
        get_chat_administrators=AsyncMock(return_value=[SimpleNamespace(user=SimpleNamespace(id=1))]),
        send_message=AsyncMock(return_value=SimpleNamespace(message_id=123)),
    )
    assert await fw.top3_ads_tick(bot) == "off"
    bot.send_message.assert_not_awaited()

    fw.set_setting("ads_top3_enabled", "1")
    fw.set_setting("ads_top3_last_sent_ts", time.time())
    assert await fw.top3_ads_tick(bot) == "waiting"
    fw.set_setting("ads_top3_force_send", "1")
    assert await fw.top3_ads_tick(bot) == "sent"
    sent = bot.send_message.await_args.kwargs
    assert "@alice" in sent["text"] and "Bob &lt;test&gt;" in sent["text"]
    assert "@charlie" in sent["text"] and "@fourth" not in sent["text"]
    assert "@admin" not in sent["text"]
    assert sent["reply_markup"].inline_keyboard[0][0].url == "https://t.me/ERRORAS1_BOT?start=invite"
    assert fw.get_setting("ads_top3_last_result") == "ok"
    assert await fw.top3_ads_tick(bot) == "gap"
    assert bot.send_message.await_count == 1

    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE users SET weekly_points=0")
    fw.set_setting("ads_top3_force_send", "1")
    assert await fw.top3_ads_tick(bot) == "gap"
    fw.set_setting("ads_top3_last_success_ts", "0")
    assert await fw.top3_ads_tick(bot) == "empty"
    assert bot.send_message.await_count == 1
    assert fw.get_setting("ads_top3_last_result") == "empty"


with tempfile.TemporaryDirectory() as td:
    asyncio.run(check(str(Path(td) / "top3.sqlite3")))
print("TOP 3 ADS offline tests passed")
