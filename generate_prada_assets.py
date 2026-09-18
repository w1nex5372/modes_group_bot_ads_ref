"""Generate original PRADA LUX community assets.

The output is deliberately an original fan-community look: black, ivory,
silver and subtle Italian tricolour accents. It does not reproduce the official
Prada triangle badge or official Prada artwork.
"""

from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "assets"
EMOJI_DIR = ASSETS / "emoji_prada"
ADS_DIR = ASSETS / "prada_ads"
PROFILE_DIR = ASSETS / "prada"

BG = (8, 8, 8, 255)
IVORY = (239, 235, 226, 255)
SILVER = (190, 192, 196, 255)
GREEN = (20, 120, 70, 255)
RED = (160, 38, 45, 255)


def pick_font(candidates, size):
    for raw in candidates:
        path = Path(raw)
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def serif(size):
    return pick_font(
        [
            r"C:\Windows\Fonts\timesbd.ttf",
            r"C:\Windows\Fonts\georgiab.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
        ],
        size,
    )


def sans(size):
    return pick_font(
        [
            r"C:\Windows\Fonts\arial.ttf",
            r"C:\Windows\Fonts\segoeui.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ],
        size,
    )


def sans_bold(size):
    return pick_font(
        [
            r"C:\Windows\Fonts\arialbd.ttf",
            r"C:\Windows\Fonts\seguisb.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        ],
        size,
    )


def fit_text(draw, text, max_width, start_size, family="sans"):
    for size in range(start_size, 9, -1):
        f = serif(size) if family == "serif" else (sans_bold(size) if family == "bold" else sans(size))
        box = draw.textbbox((0, 0), text, font=f)
        if box[2] - box[0] <= max_width:
            return f
    return sans(9)


def save_emoji(name, draw_fn):
    im = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse((8, 8, 92, 92), fill=(10, 10, 10, 245), outline=SILVER, width=3)
    draw_fn(d)
    im.save(EMOJI_DIR / f"{name}.webp", "WEBP", quality=90, method=6)


def icon_brand(d):
    f = serif(50)
    t = "P"
    box = d.textbbox((0, 0), t, font=f)
    d.text(((100 - (box[2] - box[0])) / 2, 14), t, font=f, fill=IVORY)
    d.rectangle((18, 78, 29, 82), fill=GREEN)
    d.rectangle((29, 78, 40, 82), fill=IVORY)
    d.rectangle((40, 78, 51, 82), fill=RED)
    d.line((58, 80, 82, 80), fill=SILVER, width=2)


def icon_crown(d):
    pts = [(50, 18), (76, 44), (50, 75), (24, 44)]
    d.line(pts + [pts[0]], fill=IVORY, width=4)
    d.line((50, 18, 50, 75), fill=SILVER, width=2)
    d.line((24, 44, 76, 44), fill=SILVER, width=2)


def icon_invite(d):
    d.arc((18, 30, 58, 70), 210, 510, fill=IVORY, width=5)
    d.arc((42, 30, 82, 70), 30, 330, fill=SILVER, width=5)
    d.line((42, 43, 58, 57), fill=IVORY, width=4)


def icon_share(d):
    pts = [(22, 52), (80, 22), (62, 80), (49, 59), (36, 70)]
    d.line(pts + [pts[0]], fill=IVORY, width=3)
    d.line((22, 52, 49, 59), fill=SILVER, width=3)
    d.line((49, 59, 80, 22), fill=SILVER, width=3)


def icon_trophy(d):
    d.rectangle((39, 34, 61, 61), outline=IVORY, width=4)
    d.arc((19, 31, 44, 56), 90, 270, fill=SILVER, width=4)
    d.arc((56, 31, 81, 56), 270, 90, fill=SILVER, width=4)
    d.line((50, 61, 50, 74), fill=IVORY, width=4)
    d.line((35, 76, 65, 76), fill=IVORY, width=4)


def icon_group(d):
    d.ellipse((20, 26, 44, 50), outline=IVORY, width=4)
    d.ellipse((56, 26, 80, 50), outline=SILVER, width=4)
    d.arc((14, 45, 50, 82), 190, 350, fill=IVORY, width=4)
    d.arc((50, 45, 86, 82), 190, 350, fill=SILVER, width=4)


def icon_add(d):
    d.ellipse((18, 28, 45, 55), outline=IVORY, width=4)
    d.arc((12, 48, 51, 84), 190, 350, fill=IVORY, width=4)
    d.line((64, 43, 64, 75), fill=SILVER, width=5)
    d.line((48, 59, 80, 59), fill=SILVER, width=5)


def icon_stats(d):
    d.rectangle((22, 58, 34, 78), fill=SILVER)
    d.rectangle((44, 43, 56, 78), fill=IVORY)
    d.rectangle((66, 25, 78, 78), fill=SILVER)
    d.line((18, 80, 82, 80), fill=IVORY, width=3)


def create_profile():
    size = 1024
    im = Image.new("RGBA", (size, size), BG)
    d = ImageDraw.Draw(im)
    for radius, tone in [(470, 75), (390, 55), (320, 35)]:
        d.ellipse(
            (size / 2 - radius, size / 2 - radius, size / 2 + radius, size / 2 + radius),
            outline=(tone, tone, tone, 255),
            width=2,
        )
    pts = [(512, 120), (840, 512), (512, 904), (184, 512)]
    d.line(pts + [pts[0]], fill=(130, 130, 130, 255), width=3)

    f = serif(360)
    box = d.textbbox((0, 0), "P", font=f)
    d.text(((size - (box[2] - box[0])) / 2, 210), "P", font=f, fill=IVORY)

    f1 = fit_text(d, "PRADA LUX", 800, 110, "serif")
    box = d.textbbox((0, 0), "PRADA LUX", font=f1)
    d.text(((size - (box[2] - box[0])) / 2, 650), "PRADA LUX", font=f1, fill=IVORY)

    f2 = sans(34)
    tag = "MILANO MOOD · COMMUNITY"
    box = d.textbbox((0, 0), tag, font=f2)
    d.text(((size - (box[2] - box[0])) / 2, 780), tag, font=f2, fill=SILVER)

    f3 = sans(22)
    note = "UNOFFICIAL FAN COMMUNITY"
    box = d.textbbox((0, 0), note, font=f3)
    d.text(((size - (box[2] - box[0])) / 2, 840), note, font=f3, fill=(150, 150, 150, 255))

    x = 470
    d.rectangle((x, 900, x + 28, 906), fill=GREEN)
    d.rectangle((x + 28, 900, x + 56, 906), fill=IVORY)
    d.rectangle((x + 56, 900, x + 84, 906), fill=RED)

    im.convert("RGB").save(PROFILE_DIR / "prada_lux_profile.webp", "WEBP", quality=88, method=6)


def wrap(draw, text, font_obj, width):
    words = text.split()
    lines = []
    current = ""
    for word in words:
        test = (current + " " + word).strip()
        if draw.textbbox((0, 0), test, font=font_obj)[2] > width and current:
            lines.append(current)
            current = word
        else:
            current = test
    if current:
        lines.append(current)
    return lines


def poster(title, subtitle, bullets, filename, cta=None):
    W, H = 1080, 1350
    im = Image.new("RGB", (W, H), (7, 7, 7))
    d = ImageDraw.Draw(im)
    d.rectangle((40, 40, W - 40, H - 40), outline=(150, 150, 150), width=2)
    d.rectangle((40, 40, 360, 46), fill=GREEN)
    d.rectangle((360, 40, 720, 46), fill=(235, 235, 228))
    d.rectangle((720, 40, W - 40, 46), fill=RED)

    fb = serif(72)
    brand = "PRADA LUX"
    box = d.textbbox((0, 0), brand, font=fb)
    d.text(((W - (box[2] - box[0])) / 2, 90), brand, font=fb, fill=(238, 234, 226))

    fs = sans(26)
    tag = "MILANO MOOD · UNOFFICIAL COMMUNITY"
    box = d.textbbox((0, 0), tag, font=fs)
    d.text(((W - (box[2] - box[0])) / 2, 185), tag, font=fs, fill=(160, 160, 160))

    ft = fit_text(d, title, W - 160, 62, "bold")
    d.text((80, 290), title, font=ft, fill=(245, 245, 240))
    fsub = fit_text(d, subtitle, W - 160, 36, "sans")
    d.text((80, 380), subtitle, font=fsub, fill=(190, 190, 190))

    y = 500
    fline = sans(34)
    for number, line in bullets:
        d.text((90, y), number, font=sans_bold(40), fill=(230, 230, 225))
        yy = y + 3
        for row in wrap(d, line, fline, 800):
            d.text((160, yy), row, font=fline, fill=(230, 230, 225))
            yy += 48
        y = max(y + 100, yy + 30)

    if cta:
        d.rounded_rectangle((140, H - 250, W - 140, H - 130), radius=18, outline=(215, 215, 210), width=3)
        fcta = fit_text(d, cta, W - 340, 48, "bold")
        box = d.textbbox((0, 0), cta, font=fcta)
        d.text(((W - (box[2] - box[0])) / 2, H - 220), cta, font=fcta, fill=(245, 245, 240))

    # Quiet editorial footer instead of a fake in-image Telegram button.
    footer = "MILANO · SEMPRE INSIEME"
    ff = sans(22)
    box = d.textbbox((0, 0), footer, font=ff)
    d.text(((W - (box[2] - box[0])) / 2, H - 90), footer, font=ff, fill=(125, 125, 125))
    im.save(ADS_DIR / filename, "WEBP", quality=84, method=6)


def generate_contest_poster():
    ADS_DIR.mkdir(parents=True, exist_ok=True)
    poster(
        "SAVAITĖS INVITE TOP",
        "Kviesk bendruomenės narius ir kilk savaitės TOP.",
        [
            ("01", "Asmeninė invite nuoroda = +1 naujas narys"),
            ("02", "Add Members = +1 naujas narys"),
            ("03", "Savaitės TOP atsinaujina automatiškai"),
            ("04", "Nauja savaitė prasideda pirmadienį 00:00"),
        ],
        "contest.webp",
    )


def generate_channel_promo():
    ADS_DIR.mkdir(parents=True, exist_ok=True)
    poster(
        "PRADA INFO",
        "Visa svarbiausia informacija vienoje vietoje.",
        [
            ("01", "Prisijunk prie pagrindinės grupės"),
            ("02", "Sek naujienas ir svarbią informaciją"),
            ("03", "Dalyvauk konkursuose ir savaitės TOP"),
            ("04", "Visa bendruomenė vienoje vietoje"),
        ],
        "promo.webp",
    )


def main():
    EMOJI_DIR.mkdir(parents=True, exist_ok=True)
    ADS_DIR.mkdir(parents=True, exist_ok=True)
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    for name, fn in [
        ("brand", icon_brand),
        ("crown", icon_crown),
        ("invite", icon_invite),
        ("share", icon_share),
        ("trophy", icon_trophy),
        ("group", icon_group),
        ("add", icon_add),
        ("stats", icon_stats),
    ]:
        save_emoji(name, fn)

    create_profile()
    generate_contest_poster()
    generate_channel_promo()

    print("✅ Prada luxury community assetai sugeneruoti:")
    print(f"  {PROFILE_DIR / 'prada_lux_profile.webp'}")
    print(f"  {ADS_DIR / 'contest.webp'}")
    print(f"  {ADS_DIR / 'promo.webp'}")
    print(f"  {EMOJI_DIR}/*.webp")


if __name__ == "__main__":
    main()
