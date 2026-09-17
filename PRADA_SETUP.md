# PRADA LUX theme setup

Šita tema yra atskira nuo NĖRA DROPO. NERA DROPO lieka default, todėl esamas live deploy nenulūš.

## 1. Pull

```powershell
git pull origin main
```

## 2. Prada env

Windows:

```powershell
Copy-Item .env.prada.example .env
notepad .env
```

Būtinai pakeisk:

```env
BOT_TOKEN=...
ADMIN_IDS=...
GROUP=@TAVO_PRADA_GRUPE
GROUP_CHAT=@TAVO_PRADA_GRUPE
GROUP_PUBLIC_URL=https://t.me/TAVO_PRADA_GRUPE
API_ID=...
API_HASH=...
ROSE_ADS_CHAT=@TAVO_PRADA_GRUPE
```

Palik:

```env
BRAND_THEME=prada
DB_PATH=prada_referrals.sqlite3
EMOJI_IDS_FILE=emoji_ids_prada.json
SESSION_NAME=prada_autoforward
```

Taip Prada grupės DB ir Telegram session nesimaišys su NERA DROPO.

## 3. Custom emoji

Assetai:

```text
assets/emoji_prada/brand.webp
assets/emoji_prada/crown.webp
assets/emoji_prada/invite.webp
assets/emoji_prada/share.webp
assets/emoji_prada/trophy.webp
assets/emoji_prada/group.webp
assets/emoji_prada/add.webp
assets/emoji_prada/stats.webp
```

Paleisk:

```powershell
.\setup_prada_emoji.bat
```

Bus sukurtas `emoji_ids_prada.json`.

## 4. Profilio paveikslas

Naudok:

```text
assets/prada/prada_lux_profile.webp
```

Tai originalus PRADA LUX community avataras, ne oficialus Prada logotipas.

## 5. ADS vizualai

```text
assets/prada_ads/contest.webp
assets/prada_ads/promo.webp
```

Tekstai:

```text
PRADA_CONTEST_AD.txt
PRADA_PROMO_AD.txt
```

Rose grupėje reply į image+caption:

```text
/save konkursas
/save promo
```

Patikrink:

```text
/get konkursas
/get promo
```

## 6. Paleidimas

```powershell
.\start.bat
```

Launcher automatiškai pamatys:

```env
BRAND_THEME=prada
```

ir paleis `prada_ui.py` vietoje NERA DROPO `live_ui.py`.

## 7. Auto ADS

Po vieną komandą:

```text
/adsset konkursas promo
/adsfast 15
/adson
/adsnext
```

## 8. Greitas UI testas

```text
/start
/points
/top
/alltime
/lastweek
/admin
/adpack
```

Jei visur matai `PRADA LUX`, custom black/ivory emoji ir naujus mygtukus — theme aktyvi.

## Svarbu

Tai skirta neoficialiai fanų/luxury fashion bendruomenei. Grupės aprašyme palik aiškų sakinį, kad ji nesusijusi su Prada S.p.A., ir nenaudok „official“.
