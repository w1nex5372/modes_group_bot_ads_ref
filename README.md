# modes_group_bot_ads_ref

Telegram grupės sistema su referral konkursu, savaitiniais TOP, tiesiogine boto ADS rotacija ir auto-forward.

## Kas veikia

- individuali invite nuoroda kiekvienam nariui;
- +1 už naują narį per referral link;
- +1 už tiesioginį Add Member, kai Telegram pateikia kas pridėjo;
- savaitės invite TOP 10;
- viso laiko invite TOP;
- praėjusios savaitės TOP;
- savaitės žinučių TOP 10;
- dabartiniai grupės adminai visuose TOP sąrašuose automatiškai praleidžiami;
- LIVE invite TOP grupėje;
- savaitinis resetas pirmadienį 00:00;
- vartotojo meniu ir admin panelė;
- paties boto siunčiamų reklamų rotacija su keičiamu intervalu;
- atskiras auto-forward į `chats.txt` grupes.

## Vartotojo komandos

```text
/start
/mylink
/points
/top
/msgtop
/alltime
/lastweek
/help
/how
```

## Admin komandos

```text
/admin
/stats
/liveboard
/liveboardnew
/liveboardoff
/topad
/ads
/adsset konkursas promo
/adsinterval 30
/adson
/adsoff
/adsnext
/contestad
/promoad
/adminnote
```

## Savaitės žinučių TOP

Botas skaičiuoja paprastų grupės narių savaitės žinutes ir rodo:

```text
/msgtop
```

Komandos pačios į žinučių skaičių nepatenka. Dabartiniai grupės adminai TOP lentelėje nerodomi.

## TOP adminų skip

Kiekvieną kartą generuojant TOP botas pasiima esamų grupės administratorių sąrašą ir juos praleidžia:

- LIVE invite TOP;
- `/top`;
- `/alltime`;
- `/lastweek`;
- `/msgtop`;
- savaitės pabaigos TOP 3.

## Reklamų valdymas

Atidaryk `@ERRORAS1_BOT` privačiai: `/admin` → `REKLAMŲ VALDYMAS`.
Naujos reklamos pavadinimą įrašyk pats, atsiųsk tekstą arba nuotrauką/video
su caption, pridėk URL mygtukus ir išsaugok. Botas įrašo ją į bendruomenės DB,
be Rose komandų grupėje. `VISOS REKLAMOS` leidžia peržiūrėti, redaguoti,
vieną kartą išsiųsti, pasirinkti AUTO rotacijai ir ištrinti. Įjungus AUTO,
siunčiami tik pasirinkti DB įrašai. Tiesioginiai pranešimai siunčiami be garso.

Senojoje grupėje Rose užrašai lieka Rose pusėje, bet šiame bote jų rotacija
nebenaudojama. Jei senas pavadinimas neturi išsaugoto turinio, įkelk reklamą
iš naujo. Ankstesnės šio boto išsaugotos media reklamos naudojamos per
`copyMessage` atsarginį kelią; jei pirminis privatus pranešimas nebepasiekiamas,
įkelk media iš naujo, kad būtų saugomas boto `file_id`.

## Saugumas

Nekelk į GitHub:

```text
.env
*.session
referrals.sqlite3
chats.txt
```

Tam yra `.gitignore`.

## Staged Jackie Chan Community cutover

The live systemd unit is `nera-dropo.service` and still launches `launcher.py`.
Its `.env` keeps the legacy group and `prada_referrals.sqlite3` active until
VPS Bot Manager rotates both `BOT_TOKEN` and the existing `BOT_USERNAME` to
the verified `@ERRORAS1_BOT`. Do not edit `BOT_USERNAME` alone: it is the
atomic cutover selector.

At that startup, `community_config.py` selects `JACKIE_GROUP_ID` and
`JACKIE_GROUP_PUBLIC_URL`, a fresh `jackie_community.sqlite3` (zero legacy
points, referrals and TOP), Unicode thematic emoji, and the Jackie brand.
The old database remains untouched for rollback; it is not visible in the
new bot. The new bot syncs its public name, descriptions and one JPG avatar
from `assets/jackie_shop.jpg`. `BRAND_THEME=prada` is an internal launcher
selector and must not be changed to `jackie`.

Auto-forward starts disabled (`SOURCE_MESSAGE_ID=0`) because old-group
message 1181 cannot serve as the new source. Native ADS starts disabled in the
fresh database. Enable each only after a new ad source and the forwarding
Telegram account's access to the new group have been verified. Manual channel
posting fails closed until `JACKIE_CHANNEL_CHAT` names a new channel; it never
posts the Jackie brand to the legacy Prada channel by default.

The Jackie INFO channel is `-1004344549758`. Its one-time pinned welcome post
is created with `python post_prada_channel.py info --pin`; this uses
`assets/jackie_info.png`, validates the target channel title and bot admin
post/pin rights before sending, and links to the new group and Community bot.
Do not rerun the command casually: it creates a second channel post.

To edit that existing pinned post, open `@ERRORAS1_BOT` privately as a group
admin: `/admin` → `UI REDAGUOTI` → `INFO KANALO TEKSTAS` for the body, or
`REDAGUOTI MYGTUKUS` → `CHANNEL: ...` for one of its three button labels.
Changes are stored in the fresh Jackie DB and edit the verified pinned post
in place; they do not create a duplicate or change the old Prada channel.
The fixed title and unofficial-community disclaimer remain visible.

The current Community ADS transport is the bot itself, not Rose. New
databases start with an empty rotation. An AUTO state without selected ads
fails closed; no `/get` message is sent to the group. The old Rose bot can
remain in the group for unrelated legacy use, but the Community bot does not
depend on it for advertisements.

The dashboard `JACKIE CHAN Community` card rotates only this Community bot;
it does not change PRADA Shop or the Telegram group's own title/photo.

### ADS creation inside the Community bot

In a private chat with `@ERRORAS1_BOT`, a group admin can use `/ads` or
`/admin` → `REKLAMŲ VALDYMAS`. The dashboard shows AUTO, selected ads,
last result and weekly TOP 3 state. `NAUJA REKLAMA` takes a custom name,
text or photo/video with a caption, and up to six HTTP(S) URL buttons.
`IŠSAUGOTI BOTE` persists the ad immediately; `IŠSAUGOTI IR SIŲSTI`
also queues one public post. Both avoid Rose entirely. Media from new ads
is retained by this bot's `file_id`; historical media without one uses the
original private source message if still accessible.

`VISOS REKLAMOS` lists managed ads and any legacy names without saved
content. A managed ad can be previewed, edited, sent once, selected for
AUTO or deleted. Deletion is a local tombstone; it does not send `/clear`
to Rose. Editing an ad currently in AUTO pauses rotation for review;
editing an unrelated ad does not. `/adsset`, `/adson`, `/adsoff`, and
`/adsnext` use the same verified catalog and cannot activate an empty
rotation. Delivery errors stop AUTO and notify the ad owner privately.
The public send is recorded once by message ID, with the one-shot request
cleared before transport to avoid an immediate duplicate after a crash.

### Dynamic weekly TOP 3 ad

`REKLAMŲ VALDYMAS` -> `TOP 3` (also on `VISOS REKLAMOS`) controls a native
Telegram message built from the current `users.weekly_points` table at send
time. Group administrators and configured admin IDs are excluded, matching
the public weekly leaderboard. The message contains the first three eligible
users, a link back to the Community bot's invite flow, and the unofficial
community disclaimer. It has no dependency on static ADS selection.
The scheduler is off by default, offers
1/3/6/12/24-hour intervals (default 6 hours), and does not post immediately
when enabled; `SIŲSTI DABAR` queues a one-time post. Empty rankings are skipped.
The last attempt/result/message ID are stored in the active community DB, and
a two-minute gap from regular ADS reduces back-to-back group posts. A send is
recorded before the Bot API call to avoid an immediate duplicate after a crash;
if Telegram rejects it, retry manually or at the next interval.

### Additional Jackie URL buttons

In a private chat with the Community bot, a group admin opens `/admin` ->
`UI REDAGUOTI` -> `PRIDETI MYGTUKA`. Choose a placement, send the button
label, then its HTTPS URL, and confirm the preview. `PAPILDOMI MYGTUKAI`
lists saved buttons and lets the admin remove them. The same add/list entries
are available in the existing button editor. This does not replace the fixed
buttons or the existing rename flow.

Placements are the bot's `/start` menu, personal invite message, group LIVE/ADS
message, verified pinned Jackie INFO channel post (and future INFO posts), and
the private admin menu. At most five extra URL buttons are stored per placement
in the active Jackie SQLite settings. The pinned INFO post is edited in place
only after its channel and message are verified; old unrelated messages are
not rewritten. Bot API button actions other than HTTPS links are not created
by this editor.
