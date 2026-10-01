"""Offline test of custom emoji capture, persistence, preview and direct send."""

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

root = Path(__file__).resolve().parents[1]
if not (root / "branded_bot.py").exists():
    root = root / "nera_dropo"
sys.path.append(str(root))

os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "offline")
os.environ.setdefault("BOT_TOKEN", "123456:offline")
os.environ.setdefault("GROUP", "-1004458044045")
os.environ.setdefault("GROUP_CHAT", "-1004458044045")

from telegram import MessageEntity

import ads_builder as ab
import forwarder as fw
import prada_safe as app


async def check(db):
    fw.DB_PATH = db
    fw.init_settings()
    ab.init(db)
    app.rb.DB_PATH = db

    emoji_id = "5368324170671202286"
    caption = "👋🥋 Kviečiame į dojo"
    emoji = MessageEntity(type="custom_emoji", offset=2, length=2, custom_emoji_id=emoji_id)
    label_message = SimpleNamespace(text="👋🥋 KVIETIMAI", entities=[emoji])
    label, icon_id = app.ads_button_label_with_icon(label_message)
    assert label == "👋 KVIETIMAI" and icon_id == emoji_id
    button = ab.validate_button(label, "https://example.com", icon_id)

    bot = SimpleNamespace(
        send_photo=AsyncMock(return_value=SimpleNamespace(message_id=77)),
        send_message=AsyncMock(return_value=SimpleNamespace(message_id=78)),
        send_video=AsyncMock(), copy_message=AsyncMock(),
    )
    message = SimpleNamespace(
        message_id=12, caption=caption, caption_entities=[emoji],
        photo=[SimpleNamespace(file_id="photo-file-id")], video=None,
        reply_text=AsyncMock(),
    )
    update = SimpleNamespace(
        effective_chat=SimpleNamespace(id=42, type="private"),
        effective_user=SimpleNamespace(id=42), effective_message=message,
    )
    context = SimpleNamespace(bot=bot, user_data={
        "ads_builder": {"step": "content", "note": "emoji_ad", "buttons": [button]},
    })
    with patch.object(app.rb, "is_admin_user", new_callable=AsyncMock, return_value=True):
        assert await app.ads_builder_capture(update, context, "photo", caption)
    draft = context.user_data["ads_builder"]
    assert draft["entities"][0]["custom_emoji_id"] == emoji_id
    preview = bot.send_photo.await_args.kwargs
    assert preview["caption_entities"][0].custom_emoji_id == emoji_id
    assert preview["reply_markup"].inline_keyboard[0][0].icon_custom_emoji_id == emoji_id

    # Exercise the real two-message URL-button editor, not only its helper.
    context.user_data["ads_builder"] = {
        **draft, "step": "button_label", "buttons": [], "button_index": None,
    }
    label_update = SimpleNamespace(
        effective_chat=update.effective_chat, effective_user=update.effective_user,
        effective_message=SimpleNamespace(
            text="👋🥋 KVIETIMAI", entities=[emoji], reply_text=AsyncMock(),
        ),
    )
    with patch.object(app.rb, "is_admin_user", new_callable=AsyncMock, return_value=True):
        assert await app.ads_builder_capture(label_update, context, "text", "👋🥋 KVIETIMAI")
        assert context.user_data["ads_builder"]["button_icon_id"] == emoji_id
        label_update.effective_message.text = "https://example.com"
        label_update.effective_message.entities = []
        assert await app.ads_builder_capture(label_update, context, "text", "https://example.com")
    draft = context.user_data["ads_builder"]
    assert draft["buttons"][0]["icon_custom_emoji_id"] == emoji_id

    ab.save_direct(
        db, draft["note"], 42, draft["source_message_id"], draft["kind"], draft["body"],
        buttons=draft["buttons"], source_chat_id=42,
        media_file_id=draft["media_file_id"], entities=draft["entities"],
    )
    row = ab.active_by_note(db, "emoji_ad")
    assert row["entities_json"] != "[]"
    bot.send_photo.reset_mock()
    assert await fw.send_direct_ad(bot, "emoji_ad", "normal")
    sent = bot.send_photo.await_args.kwargs
    assert sent["caption"] == caption
    assert sent["caption_entities"][0].custom_emoji_id == emoji_id
    assert sent["reply_markup"].inline_keyboard[0][0].icon_custom_emoji_id == emoji_id

    text = "🥋 Text"
    text_entity = {"type": "custom_emoji", "offset": 0, "length": 2, "custom_emoji_id": emoji_id}
    ab.save_direct(db, "text_ad", 42, 13, "text", text, entities=[text_entity])
    assert await fw.send_direct_ad(bot, "text_ad", "normal")
    assert bot.send_message.await_args.kwargs["entities"][0].custom_emoji_id == emoji_id


with tempfile.TemporaryDirectory() as td:
    asyncio.run(check(str(Path(td) / "custom-emoji.sqlite3")))
print("Custom emoji ADS offline tests passed")
