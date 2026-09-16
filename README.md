# modes_group_bot_ads_ref

Telegram grupės sistema su referral konkursu, savaitiniais TOP, Rose ADS rotacija ir auto-forward.

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
- Rose saved-note rotacija su keičiamu intervalu;
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

## Rose ADS savaitės startas

Pirma susikurk 2 Rose notes:

```text
konkursas
promo
```

Paruoštus tekstus sugeneruoja pats botas:

```text
/contestad
/promoad
```

Tada:

```text
/adsset konkursas promo
/adsinterval 30
/adson
/adsnext
```

Plačiau žr. `WEEKLY_ADS_SETUP.txt`.

## Saugumas

Nekelk į GitHub:

```text
.env
*.session
referrals.sqlite3
chats.txt
```

Tam yra `.gitignore`.
