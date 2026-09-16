# TG GROUP SYSTEM V4 — SIMPLE + LIVE TOP 10

## Ką reikia redaguoti?

Tik **2 failus**.

### `.env`

```env
BOT_TOKEN=TAVO_BOT_TOKEN
GROUP=@NERADAUDROPO
API_ID=TAVO_API_ID
API_HASH=TAVO_API_HASH
SOURCE_MESSAGE_ID=0
```

Jei nori vieną konkretų reklamos postą forwardinti į `chats.txt` grupes,
`SOURCE_MESSAGE_ID=0` pakeisk tikru posto ID.

### `chats.txt`

Čia rašai tik tikslines reklamos grupes:

```text
@grupe1
@grupe2
https://t.me/grupe3
```

Jei auto-forward į kitas grupes nereikalingas, palik tuščią.

Kitų `.py` failų redaguoti nereikia.

## Kur matysis referral sistema?

Žmogus atidaro tavo BotFather sukurtą botą ir parašo `/start`.
Ten matys:

- Mano invite
- Mano taškai
- Savaitės TOP
- Viso TOP
- Praeita savaitė

Paspaudęs **Mano invite** gauna individualią grupės invite nuorodą.
Naujas narys per ją = +1 taškas.

## LIVE TOP 10 grupėje

Botas grupėje automatiškai sukuria vieną LIVE leaderboard postą.
Po naujo taško jis atsinaujina iškart, papildomai tikrinamas kas 60 s.

Pavyzdys:

```text
🏆 SAVAITĖS INVITE TOP 10 — LIVE

🥇 @user1 — 14 tšk.
🥈 @user2 — 11 tšk.
🥉 @user3 — 8 tšk.
4. @user4 — 6 tšk.
...
10. @user10 — 1 tšk.

🔄 Reset: pirmadienį 00:00
```

Po sąrašu yra mygtukas **DALYVAUTI / GAUTI INVITE**, vedantis į referral botą.

Admin komandos:

```text
/liveboard       - įjungti / atnaujinti LIVE TOP
/liveboardnew    - sukurti naują LIVE TOP postą
/liveboardoff    - sustabdyti automatinį update
/topad           - įmesti papildomą TOP 10 reklaminį postą dabar
```

## Rose ADS

```text
/adsset ads konkursas promo
/adsinterval 10
/adson
```

## Paleidimas

`start.bat`
