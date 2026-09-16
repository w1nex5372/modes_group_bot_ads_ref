import asyncio

import forwarder as f


async def main():
    f.init_settings()
    client = f.TelegramClient(f.SESSION_NAME, f.API_ID, f.API_HASH)
    print("Jungiamasi prie Telegram user automation...")
    await client.connect()
    try:
        if not await client.is_user_authorized():
            print("\n❌ Telegram user session dar neprijungta.")
            print("Paleisk dvi komandas:")
            print("  telegram_login.bat send +3706XXXXXXX")
            print("  telegram_login.bat finish GAUTAS_KODAS")
            print("Tada paleisk run_forward_only.bat dar kartą.\n")
            return

        me = await client.get_me()
        print(f"Prisijungta kaip: {me.first_name or ''} (@{me.username or 'be_username'})")
        print(f"Rose ADS grupė: {f.ROSE_ADS_CHAT}")
        print("FAST ADS intervalas valdomas /adsfast, NORMAL — /adsinterval")
        print("CTRL+C sustabdyti.\n")
        await asyncio.gather(f.forward_loop(client), f.rose_ads_loop(client))
    finally:
        await client.disconnect()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nSustabdyta.")
