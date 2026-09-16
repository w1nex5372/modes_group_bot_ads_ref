# Security

Niekada nekelk į GitHub:

- `.env`
- Telegram `*.session`
- `referrals.sqlite3`
- kitų failų su bot tokenais, API hash ar prisijungimo sesijomis.

Šie failai yra `.gitignore`.

## Jei paslaptis jau buvo įkelta į viešą repo

Vien failo ištrynimo neužtenka: reikšmė lieka Git istorijoje.

Rekomenduojama:

1. Per `@BotFather` atšaukti seną bot tokeną ir sugeneruoti naują.
2. Naują tokeną laikyti tik lokaliame `.env`.
3. Telegram session failų niekur nekelti.
4. Jei repo nereikia viešo, pakeisti jį į Private.
