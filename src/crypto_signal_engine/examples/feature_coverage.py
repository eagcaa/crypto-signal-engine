import argparse
import asyncio

from crypto_signal_engine.config.settings import get_settings
from crypto_signal_engine.db import (
    create_database_engine,
    create_session_factory,
    initialize_database,
)
from crypto_signal_engine.db.research_feature_repository import (
    ResearchFeatureRepository,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Show persisted research feature coverage."
    )
    parser.add_argument("--symbol", default="BTCUSDT")
    return parser.parse_args()


async def run(symbol: str) -> None:
    settings = get_settings()
    engine = create_database_engine(settings.database_url)
    await initialize_database(engine)

    try:
        repository = ResearchFeatureRepository(
            create_session_factory(engine)
        )
        coverage = await repository.coverage(symbol)

        usable_ratio = (
            coverage.usable_rows / coverage.rows * 100
            if coverage.rows
            else 0.0
        )

        print(
            "FEATURE_COVERAGE "
            f"symbol={coverage.symbol} "
            f"rows={coverage.rows} "
            f"usable_rows={coverage.usable_rows} "
            f"usable_ratio={usable_ratio:.2f}% "
            f"span_hours={coverage.span_hours:.2f} "
            f"first={coverage.first_timestamp.isoformat() if coverage.first_timestamp else 'n/a'} "
            f"last={coverage.last_timestamp.isoformat() if coverage.last_timestamp else 'n/a'}"
        )
    finally:
        await engine.dispose()


def main() -> None:
    args = parse_args()
    asyncio.run(run(args.symbol))


if __name__ == "__main__":
    main()
