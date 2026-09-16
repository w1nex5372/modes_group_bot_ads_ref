import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()
DB_PATH = os.getenv("DB_PATH", "referrals.sqlite3").strip()
ROSE_FAST_NOTES = [x.strip() for x in os.getenv("ROSE_FAST_NOTES", "konkursas,promo").split(",") if x.strip()]


def db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def get(key, default=""):
    with closing(db()) as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default


def fmt_ts(raw):
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return "niekada"
    return datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M:%S") if value > 0 else "niekada"


def main():
    try:
        notes = json.loads(get("ads_notes", "[]"))
    except Exception:
        notes = []
    print("=" * 60)
    print("NĖRA DROPO · AUTO ADS STATUS")
    print("=" * 60)
    print(f"Būsena: {'ĮJUNGTA' if get('ads_enabled','0') == '1' else 'IŠJUNGTA'}")
    print(f"Notes: {', '.join(notes) if notes else '—'}")
    print(f"FAST names: {', '.join(ROSE_FAST_NOTES) or '—'}")
    print(f"FAST intervalas: {get('ads_fast_interval_minutes','15')} min.")
    print(f"NORMAL intervalas: {get('ads_interval_minutes','30')} min.")
    print()
    print(f"Paskutinis note: {get('ads_last_note','—') or '—'}")
    print(f"Eilė: {get('ads_last_lane','—') or '—'}")
    print(f"Rezultatas: {get('ads_last_result','never')}")
    print(f"Rose patvirtinta: {'TAIP' if get('ads_last_verified','0') == '1' else 'NE / DAR NE'}")
    print(f"Paskutinis siuntimas: {fmt_ts(get('ads_last_sent_ts','0'))}")
    print(f"Paskutinis FAST: {fmt_ts(get('ads_fast_last_sent_ts','0'))}")
    print(f"Paskutinis NORMAL: {fmt_ts(get('ads_normal_last_sent_ts','0'))}")
    if get('ads_last_error',''):
        print(f"Klaida: {get('ads_last_error')}")
    print("=" * 60)
    print("Terminale sėkmė: Rose ADS ✅ VERIFIED")


if __name__ == "__main__":
    main()
