"""Select the staged community configuration at token rotation.

The VPS manager writes BOT_TOKEN and an existing BOT_USERNAME in one atomic
environment-file update.  Until the verified username is the new bot, the
running and restarted legacy service keeps its original group and database.
"""

import os


def apply_staged_community_config() -> bool:
    expected = os.getenv("JACKIE_BOT_USERNAME", "").strip().lstrip("@").casefold()
    current = os.getenv("BOT_USERNAME", "").strip().lstrip("@").casefold()
    if not expected or current != expected:
        return False

    group_id = os.getenv("JACKIE_GROUP_ID", "").strip()
    group_url = os.getenv("JACKIE_GROUP_PUBLIC_URL", "").strip()
    if not group_id.startswith("-100") or not group_id[1:].isdigit():
        raise RuntimeError("JACKIE_GROUP_ID must be a Telegram supergroup ID")
    if not group_url.startswith("https://t.me/"):
        raise RuntimeError("JACKIE_GROUP_PUBLIC_URL must be a Telegram link")

    os.environ.update({
        "GROUP": group_id,
        "GROUP_CHAT": group_id,
        "GROUP_PUBLIC_URL": group_url,
        "SOURCE_CHAT": group_id,
        "SOURCE_MESSAGE_ID": "0",  # No old-group post can be forwarded.
        "ROSE_ADS_CHAT": group_id,
        "DB_PATH": "jackie_community.sqlite3",  # Fresh points, invites and TOP.
        "PRADA_BRAND_NAME": "JACKIE CHAN",
        "PRADA_GROUP_LABEL": "JACKIE CHAN NAMAI",
        "PRADA_DISCLAIMER": "Neoficiali bendruomenė · nesusijusi su Jackie Chanu.",
        "PRADA_BUTTON_CUSTOM_EMOJI": "0",
        "EMOJI_IDS_FILE": "emoji_ids_jackie.json",
        "BOT_PUBLIC_URL": f"https://t.me/{expected}",
        "CHANNEL_CHAT": os.getenv("JACKIE_CHANNEL_CHAT", "").strip(),
    })
    return True
