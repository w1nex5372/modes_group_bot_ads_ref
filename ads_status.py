import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()

DB_PATH = os.getenv("DB_PATH", "referrals.sqlite3").strip()
ROSE_FAST_NOTES = [
    item.strip()
    for item in os.getenv("ROSE_FAST_NOTES", "konkursas,promo").split(",")
    if item.strip()
]
ROSE_FAST_INTERVAL_MINUTES = max(
    5, int(os.getenv("ROSE_FAST_INTERVAL_MINUTES", "15"))
)


def db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def get_setting(key, default=""):
    with closing(db()) as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key=?",
            (key,),
        ).fetchone()
        return row["value"] if row else default


def fmt_ts(raw):
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return "niekada"
    if value <= 0:
        return "niekada"
    return datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M:%S")


def main():
    try:
        notes = json.loads(get_setting("ads_notes", "[]"))
    except Exception:
        notes = []

    enabled = get_setting("ads_enabled", "0") == "1"
    normal_interval = get_setting("ads_interval_minutes", "30")
    verified = get_setting("ads_last_verified", "0") == "1"

    print("=" * 58)
    print("ROSE ADS STATUS")
    print("=" * 58)
    print(f"Būsena: {'ĮJUNGTA' if enabled else 'IŠJUNGTA'}")
    print(f"Visi /adsset notes: {', '.join(notes) if notes else '—'}")
    print(
        f"FAST notes: {', '.join(ROSE_FAST_NOTES) if ROSE_FAST_NOTES else '—'} "
        f"(vienas iš jų kas {ROSE_FAST_INTERVAL_MINUTES} min.)"
    )
    print(f"Kiti notes: vienas kas {normal_interval} min.")
    print()
    print(f"Paskutinis note: {get_setting('ads_last_note', '—') or '—'}")
    print(f"Eilė: {get_setting('ads_last_lane', '—') or '—'}")
    print(f"Rezultatas: {get_setting('ads_last_result', 'never')}")
    print(f"Rose atsakymas patvirtintas: {'TAIP' if verified else 'NE / DAR NE'}")
    print(f"Paskutinis siuntimas: {fmt_ts(get_setting('ads_last_sent_ts', '0'))}")
    print(f"Paskutinis FAST: {fmt_ts(get_setting('ads_fast_last_sent_ts', '0'))}")
    print(f"Paskutinis NORMAL: {fmt_ts(get_setting('ads_normal_last_sent_ts', '0'))}")
    response_id = get_setting("ads_last_response_id", "")
    if response_id:
        print(f"Rose response message ID: {response_id}")
    error = get_setting("ads_last_error", "")
    if error:
        print(f"Paskutinė klaida: {error}")
    print("=" * 58)
    print("Terminale sėkmę žymi: Rose ADS ✅ VERIFIED")


if __name__ == "__main__":
    main()
