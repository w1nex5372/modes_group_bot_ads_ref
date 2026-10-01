"""Legacy filename: native ADS fails closed when a selected ad is absent."""

import asyncio
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "offline")
os.environ.setdefault("BOT_TOKEN", "123456:offline")

import ads_builder
import forwarder


async def check(db):
    forwarder.DB_PATH = db
    forwarder.init_settings()
    ads_builder.init(db)
    bot = SimpleNamespace(send_message=AsyncMock())
    forwarder.set_setting("ads_enabled", "1")
    forwarder.set_setting("ads_notes", '["missing"]')
    forwarder.set_setting("ads_force_send", "1")
    assert await forwarder.direct_ads_tick(bot) == "skipped"
    assert forwarder.get_setting("ads_enabled") == "0"
    assert forwarder.get_setting("ads_last_result") == "missing_ad"
    bot.send_message.assert_not_awaited()


with tempfile.TemporaryDirectory() as td:
    asyncio.run(check(str(Path(td) / "ads.sqlite3")))
print("Native ADS missing-ad failure path passed")
