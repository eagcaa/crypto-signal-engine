import asyncio

from crypto_signal_engine.config.settings import get_settings
from crypto_signal_engine.db import (
    PredictionRepository,
    create_database_engine,
    create_session_factory,
    initialize_database,
)


async def run(symbol: str = "BTCUSDT") -> None:
    settings = get_settings()
    engine = create_database_engine(settings.database_url)
    await initialize_database(engine)

    try:
        repository = PredictionRepository(
            create_session_factory(engine)
        )
        rows = await repository.evaluation_provenance_stats(
            symbol=symbol
        )

        print(f"EVALUATION PROVENANCE symbol={symbol.upper()}")
        if not rows:
            print("no evaluations")
            return

        for row in rows:
            print(
                f"source={row.evaluation_source} "
                f"version={row.evaluation_version} "
                f"total={row.total} "
                f"TP={row.take_profit} "
                f"SL={row.stop_loss} "
                f"no_touch={row.expired_no_touch} "
                f"no_data={row.expired_without_data}"
            )
    finally:
        await engine.dispose()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
