"""Validated, DB-stored URL buttons for Jackie-controlled message surfaces."""

import json
import re
import secrets
from urllib.parse import urlsplit


SETTING_KEY = "jackie_extra_buttons_v1"
HIDDEN_KEY = "jackie_hidden_buttons_v1"
MAX_PER_PLACE = 5
PLACES = {
    "home": "/start meniu",
    "invite": "Mano invite žinutė",
    "live": "Grupės LIVE / ADS",
    "channel": "INFO kanalas ir nauji kanalo įrašai",
    "admin": "Admin meniu",
}

BUILTIN_PLACES = {
    "invite": "home", "points": "home", "top": "home",
    "alltime": "home", "lastweek": "home", "info": "home",
    "admin": "home", "share": "invite", "open_group": "invite",
    "live_invite": "live", "live_group": "live",
    "channel_group": "channel", "channel_invite": "channel",
    "channel_bot": "channel",
}


def parse_hidden(raw):
    if not raw:
        return set()
    try:
        keys = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("Paslėptų mygtukų duomenys sugadinti") from exc
    if (not isinstance(keys, list) or len(keys) > len(BUILTIN_PLACES)
            or any(not isinstance(key, str) or key not in BUILTIN_PLACES for key in keys)
            or len(keys) != len(set(keys))):
        raise ValueError("Paslėptų mygtukų duomenys netinkami")
    return set(keys)


def encode_hidden(keys):
    return json.dumps(sorted(keys), ensure_ascii=False, separators=(",", ":"))


def validate_label(value):
    if not isinstance(value, str):
        raise ValueError("Mygtuko tekstas netinkamas")
    label = value.strip()
    if not 1 <= len(label) <= 64 or any(ord(char) < 32 for char in label):
        raise ValueError("Mygtuko tekstas turi būti 1–64 simbolių, vienoje eilutėje")
    return label


def validate_url(value):
    if not isinstance(value, str):
        raise ValueError("Nuoroda netinkama")
    url = value.strip()
    if not 9 <= len(url) <= 300 or any(char.isspace() or ord(char) < 32 for char in url):
        raise ValueError("Įvesk pilną HTTPS nuorodą be tarpų")
    parsed = urlsplit(url)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Nuorodos prievadas netinkamas") from exc
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or port is not None or "." not in parsed.hostname or parsed.fragment):
        raise ValueError("Leidžiamos tik pilnos HTTPS nuorodos (pvz., https://t.me/...)")
    return url


def parse_buttons(raw):
    if not raw:
        return []
    try:
        items = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("Papildomų mygtukų duomenys sugadinti") from exc
    if not isinstance(items, list) or len(items) > len(PLACES) * MAX_PER_PLACE:
        raise ValueError("Papildomų mygtukų duomenys netinkami")
    seen = set()
    counts = {place: 0 for place in PLACES}
    for item in items:
        if not isinstance(item, dict) or set(item) != {"id", "place", "label", "url"}:
            raise ValueError("Papildomų mygtukų įrašas netinkamas")
        identifier, place = item["id"], item["place"]
        if (not isinstance(identifier, str) or not re.fullmatch(r"[0-9a-f]{12}", identifier)
                or identifier in seen or not isinstance(place, str) or place not in PLACES):
            raise ValueError("Papildomų mygtukų įrašas netinkamas")
        if validate_label(item["label"]) != item["label"] or validate_url(item["url"]) != item["url"]:
            raise ValueError("Papildomų mygtukų įrašas netinkamas")
        seen.add(identifier)
        counts[place] += 1
        if counts[place] > MAX_PER_PLACE:
            raise ValueError("Per daug mygtukų vienoje vietoje")
    return items


def add_button(items, place, label, url):
    if place not in PLACES:
        raise ValueError("Nežinoma mygtuko vieta")
    label = validate_label(label)
    url = validate_url(url)
    if sum(item["place"] == place for item in items) >= MAX_PER_PLACE:
        raise ValueError("Šioje vietoje jau yra daugiausia papildomų mygtukų")
    identifier = secrets.token_hex(6)
    while any(item["id"] == identifier for item in items):
        identifier = secrets.token_hex(6)
    return [*items, {"id": identifier, "place": place, "label": label, "url": url}]


def remove_button(items, identifier):
    changed = [item for item in items if item["id"] != identifier]
    if len(changed) == len(items):
        raise ValueError("Mygtukas nerastas; atnaujink sąrašą")
    return changed


def buttons_for(items, place):
    return [item for item in items if item["place"] == place]


def encode_buttons(items):
    return json.dumps(items, ensure_ascii=False, separators=(",", ":"))
