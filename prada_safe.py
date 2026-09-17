"""Safe PRADA LUX runtime wrapper.

Keeps custom emoji icons on inline buttons, but uses plain Unicode symbols in
message text. This avoids Telegram `Entity_text_invalid` errors from custom
emoji HTML entities while preserving the branded Prada button UI.
"""

import os

import prada_ui as p
import referral_bot as rb


# Keep a private copy for inline-button icons before disabling custom-emoji
# entities in normal message text.
_PRADA_EMOJI_IDS = dict(rb.EMOJI_IDS)
_USE_BUTTON_CUSTOM = os.getenv("PRADA_BUTTON_CUSTOM_EMOJI", "1").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}


def safe_icon_id(key):
    if not _USE_BUTTON_CUSTOM:
        return None
    value = _PRADA_EMOJI_IDS.get(key)
    if value is None:
        return None
    value = str(value).strip()
    return value or None


# prada_ui.button() resolves icon_id from its own module globals at call time.
p.icon_id = safe_icon_id

# rb.em() now emits the Prada Unicode fallbacks only, so Telegram HTML has no
# custom emoji entities to reject. Inline keyboard icons still use the IDs above.
rb.EMOJI_IDS = {}


if __name__ == "__main__":
    print("PRADA LUX safe emoji mode: text=unicode, buttons=custom" if _USE_BUTTON_CUSTOM else "PRADA LUX safe emoji mode: text=unicode, buttons=unicode")
    p.bb.main()
