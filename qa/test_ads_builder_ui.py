"""Offline production-shaped private admin flow."""

import asyncio
import os
import sqlite3
import tempfile
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.update({
    "BOT_TOKEN": "123456:offline",
    "GROUP": "-1004458044045",
    "GROUP_CHAT": "-1004458044045",
    "DB_PATH": "offline.sqlite3",
    "API_ID": "12345",
    "API_HASH": "offline",
    "PRADA_BRAND_NAME": "JACKIE CHAN",
    "JACKIE_BOT_USERNAME": "ERRORAS1_BOT",
    "BOT_USERNAME": "ERRORAS1_BOT",
})

import prada_safe as app


async def check(db):
    app.rb.DB_PATH = db
    app.rb.init_db()
    user = SimpleNamespace(id=42)
    chat = SimpleNamespace(id=42, type="private")
    query = SimpleNamespace(
        data="adsb_start", from_user=user,
        message=SimpleNamespace(chat=chat, reply_text=AsyncMock()),
        answer=AsyncMock(), edit_message_text=AsyncMock(),
    )
    bot = SimpleNamespace(
        copy_message=AsyncMock(), send_message=AsyncMock(), send_photo=AsyncMock(), send_video=AsyncMock(),
        get_chat_administrators=AsyncMock(return_value=[SimpleNamespace(user=SimpleNamespace(id=1))]),
    )
    context = SimpleNamespace(bot=bot, user_data={})
    update = SimpleNamespace(callback_query=query)
    with patch.object(app.rb, "is_admin_user", new_callable=AsyncMock, return_value=True):
        await app.on_button(update, context)
        menu = query.edit_message_text.await_args.kwargs["reply_markup"]
        labels = [button.text for row in menu.inline_keyboard for button in row]
        assert "KONKURSAS" not in labels and "PROMO" not in labels
        assert any("ATGAL" in label for label in labels)
        query.data = "adsb_name_custom"
        await app.on_button(update, context)
        assert any("ATGAL" in button.text for row in query.edit_message_text.await_args.kwargs["reply_markup"].inline_keyboard for button in row)
        name_message = SimpleNamespace(
            message_id=74, text="promo", caption=None, video=None,
            reply_text=AsyncMock(),
        )
        private_update = SimpleNamespace(
            effective_chat=chat, effective_user=user, effective_message=name_message,
        )
        await app.ads_builder_input(private_update, context)
        message = SimpleNamespace(
            message_id=75, text=None, caption="My photo caption", video=None,
            photo=[SimpleNamespace(file_id="fake-photo-id")],
            reply_text=AsyncMock(),
        )
        private_update.effective_message = message
        await app.ads_builder_media(private_update, context)
        assert context.user_data["ads_builder"]["step"] == "confirm"
        bot.send_photo.assert_awaited_once()
        query.data = "adsb_buttons"
        await app.on_button(update, context)
        query.data = "adsb_ba"
        await app.on_button(update, context)
        message.text = "Open shop"
        await app.ads_builder_input(private_update, context)
        message.text = "https://example.com/store"
        await app.ads_builder_input(private_update, context)
        assert len(context.user_data["ads_builder"]["buttons"]) == 1
        assert bot.send_photo.await_count == 2
        query.data = "adsb_launch"
        await app.on_button(update, context)
        assert context.user_data.get("ads_builder") is None
        assert app.ads_builder.latest(db, 42)["status"] == "verified"
        assert app.rb.get_setting("ads_force_job_id") == "1"
        assert app.ads_builder.latest(db, 42)["media_file_id"] == "fake-photo-id"
        assert "Open shop" in app.ads_builder.latest(db, 42)["buttons_json"]
        # A duplicate confirmation cannot enqueue a second job.
        await app.on_button(update, context)
        assert app.ads_builder.latest(db, 42)["id"] == 1
        app.rb.set_setting("ads_force_job_id", "0")
        query.data = "adsb_view_1"
        await app.on_button(update, context)
        assert any("SIŲSTI DABAR" in button.text for row in query.edit_message_text.await_args.kwargs["reply_markup"].inline_keyboard for button in row)
        query.data = "adsb_sendask_1"
        await app.on_button(update, context)
        query.data = "adsb_sendconfirm_1"
        await app.on_button(update, context)
        assert app.rb.get_setting("ads_force_job_id") == "1"
        app.rb.set_setting("ads_force_job_id", "0")
        app.rb.set_setting("ads_enabled", "0")
        query.data = "admin_ads_on"
        await app.on_button(update, context)
        assert app.rb.get_setting("ads_enabled") == "0"
        query.data = "adsb_list_0"
        await app.on_button(update, context)
        assert "ESAMI ADS" in query.edit_message_text.await_args.args[0]
        query.data = "adsb_toggle_1"
        await app.on_button(update, context)
        assert app.ads_builder_selected() == ["promo"]
        query.data = "adsb_auto_start"
        await app.on_button(update, context)
        assert app.rb.get_setting("ads_enabled") == "1"
        assert app.rb.get_setting("ads_notes") == '["promo"]'
        query.data = "adsb_fast_30"
        await app.on_button(update, context)
        assert app.rb.get_setting("ads_fast_interval_minutes") == "30"
        query.data = "adsb_deleteask_1"
        await app.on_button(update, context)
        query.data = "adsb_deleteconfirm"
        await app.on_button(update, context)
        assert app.ads_builder.latest(db, 42)["operation"] == "delete"
        assert app.ads_builder.latest(db, 42)["status"] == "deleted"
        assert app.rb.get_setting("ads_enabled") == "0"
        command_update = SimpleNamespace(effective_message=SimpleNamespace(reply_text=AsyncMock()))
        with patch.object(app.rb, "require_admin", new_callable=AsyncMock, return_value=True):
            await app.ads_on_cmd(command_update, context)
        assert app.rb.get_setting("ads_enabled") == "0"
        query.data = "adsb_top3_toggle"
        await app.on_button(update, context)
        assert app.rb.get_setting("ads_top3_enabled") == "1"
        assert float(app.rb.get_setting("ads_top3_last_sent_ts")) > 0
        query.data = "adsb_top3_interval_180"
        await app.on_button(update, context)
        assert app.rb.get_setting("ads_top3_interval_minutes") == "180"
        query.data = "adsb_top3_now"
        await app.on_button(update, context)
        assert app.rb.get_setting("ads_top3_force_send") == "1"
        with sqlite3.connect(db) as conn:
            conn.execute("INSERT INTO users(user_id,username,first_name,weekly_points,created_at) VALUES (1,'admin','Admin',10,'now')")
            conn.execute("INSERT INTO users(user_id,username,first_name,weekly_points,created_at) VALUES (2,'fan','Fan',3,'now')")
        query.data = "adsb_top3_preview"
        await app.on_button(update, context)
        preview = query.message.reply_text.await_args.args[0]
        assert "@fan" in preview and "@admin" not in preview


with tempfile.TemporaryDirectory() as td:
    asyncio.run(check(str(Path(td) / "ads.sqlite3")))
print("ADS builder UI offline test passed")
