import asyncio

from crypto_signal_engine.config.settings import Settings, get_settings
from crypto_signal_engine.examples.live_market_snapshot import main as live_main


def validate_railway_candidate_settings(settings: Settings) -> None:
    if settings.paper_trading_enabled:
        raise RuntimeError(
            "Railway candidate worker requires PAPER_TRADING_ENABLED=false"
        )
    if not settings.candidate_paper_enabled:
        raise RuntimeError(
            "Railway candidate worker requires CANDIDATE_PAPER_ENABLED=true"
        )
    if "localhost" in settings.database_url or "127.0.0.1" in settings.database_url:
        raise RuntimeError(
            "Railway candidate worker requires a remote DATABASE_URL"
        )
    if settings.telegram_enabled and (
        not settings.telegram_bot_token or not settings.telegram_chat_id
    ):
        raise RuntimeError(
            "TELEGRAM_ENABLED=true requires TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID"
        )


async def run() -> None:
    settings = get_settings()
    validate_railway_candidate_settings(settings)
    print(
        "RAILWAY_CANDIDATE_READY "
        f"candidate={settings.candidate_paper_name} "
        "paper_trading=false exchange_orders=disabled"
    )
    await live_main()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
