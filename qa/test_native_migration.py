"""Old Rose settings and catalog survive native-transport cutover safely."""

import os
import sqlite3
import tempfile
from pathlib import Path

os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "offline")
os.environ.setdefault("BOT_TOKEN", "123456:offline")

import ads_builder
import forwarder


with tempfile.TemporaryDirectory() as td:
    db = str(Path(td) / "old.sqlite3")
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
        conn.executemany("INSERT INTO settings VALUES (?,?)", [
            ("ads_enabled", "1"), ("ads_notes", '["konkursas"]'),
            ("ads_force_send", "1"), ("ads_top3_enabled", "1"),
        ])
    forwarder.DB_PATH = db
    ads_builder.init(db)
    old_id = ads_builder.enqueue(db, "konkursas", 42, 12, "photo", "Old body", False)
    ads_builder.claim(db)
    ads_builder.finish(db, old_id, "verified")

    forwarder.init_settings()
    assert forwarder.get_setting("ads_enabled") == "0"
    assert forwarder.get_setting("ads_notes") == "[]"
    assert forwarder.get_setting("ads_force_send") == "0"
    assert forwarder.get_setting("ads_top3_enabled") == "1"
    assert forwarder.get_setting("ads_transport_mode") == "native_v1"
    assert ads_builder.active_by_note(db, "konkursas")["id"] == old_id
    assert ads_builder.active_by_note(db, "konkursas")["media_file_id"] == ""

    forwarder.set_setting("ads_enabled", "1")
    forwarder.set_setting("ads_notes", '["konkursas"]')
    forwarder.init_settings()
    assert forwarder.get_setting("ads_enabled") == "1"
    assert forwarder.get_setting("ads_notes") == '["konkursas"]'

print("Native ADS migration offline test passed")
