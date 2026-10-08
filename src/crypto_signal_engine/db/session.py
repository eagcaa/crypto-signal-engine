from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from crypto_signal_engine.db.models import Base


def create_database_engine(database_url: str) -> AsyncEngine:
    return create_async_engine(
        database_url,
        pool_pre_ping=True,
    )


def create_session_factory(
    engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        engine,
        expire_on_commit=False,
    )


async def initialize_database(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text("CREATE EXTENSION IF NOT EXISTS timescaledb")
        )
        await connection.run_sync(Base.metadata.create_all)

        await connection.execute(
            text(
                """
                ALTER TABLE prediction_evaluations
                ADD COLUMN IF NOT EXISTS status VARCHAR(32)
                NOT NULL DEFAULT 'evaluated'
                """
            )
        )
        await connection.execute(
            text(
                """
                ALTER TABLE prediction_evaluations
                ALTER COLUMN exit_price DROP NOT NULL
                """
            )
        )
        await connection.execute(
            text(
                """
                ALTER TABLE prediction_evaluations
                ALTER COLUMN return_pct DROP NOT NULL
                """
            )
        )
        await connection.execute(
            text(
                """
                ALTER TABLE prediction_evaluations
                ALTER COLUMN success DROP NOT NULL
                """
            )
        )
        await connection.execute(
            text(
                """
                ALTER TABLE prediction_evaluations
                ADD COLUMN IF NOT EXISTS outcome VARCHAR(32)
                """
            )
        )
        await connection.execute(
            text(
                """
                ALTER TABLE prediction_evaluations
                ADD COLUMN IF NOT EXISTS label INTEGER
                """
            )
        )
        await connection.execute(
            text(
                """
                ALTER TABLE predictions
                ADD COLUMN IF NOT EXISTS feature_contributions_json VARCHAR(4096)
                """
            )
        )
        await connection.execute(
            text(
                """
                ALTER TABLE predictions
                ADD COLUMN IF NOT EXISTS reason VARCHAR(2048)
                """
            )
        )
        await connection.execute(
            text(
                """
                ALTER TABLE research_feature_snapshots
                ADD COLUMN IF NOT EXISTS history_seconds INTEGER
                NOT NULL DEFAULT 0
                """
            )
        )

        await connection.execute(
            text(
                """
                SELECT create_hypertable(
                    'market_snapshots',
                    'timestamp',
                    if_not_exists => TRUE
                )
                """
            )
        )
