import logging

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from crypto_signal_engine.db.models import Base

logger = logging.getLogger(__name__)


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


async def _try_enable_timescaledb(engine: AsyncEngine) -> bool:
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("CREATE EXTENSION IF NOT EXISTS timescaledb")
            )
        return True
    except SQLAlchemyError as exc:
        logger.warning(
            "TimescaleDB extension unavailable; continuing with standard PostgreSQL: %s",
            exc,
        )
        return False


async def _try_make_market_snapshots_hypertable(
    engine: AsyncEngine,
) -> None:
    try:
        async with engine.begin() as connection:
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
    except SQLAlchemyError as exc:
        logger.warning(
            "TimescaleDB hypertable setup unavailable; "
            "market_snapshots will remain a normal PostgreSQL table: %s",
            exc,
        )


async def initialize_database(engine: AsyncEngine) -> None:
    timescaledb_enabled = await _try_enable_timescaledb(engine)

    async with engine.begin() as connection:
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
                ALTER TABLE prediction_evaluations
                ADD COLUMN IF NOT EXISTS gross_return_pct NUMERIC(20, 12)
                """
            )
        )
        await connection.execute(
            text(
                """
                ALTER TABLE prediction_evaluations
                ADD COLUMN IF NOT EXISTS trading_cost_pct NUMERIC(20, 12)
                """
            )
        )
        await connection.execute(
            text(
                """
                ALTER TABLE prediction_evaluations
                ADD COLUMN IF NOT EXISTS evaluation_source VARCHAR(32)
                NOT NULL DEFAULT 'unknown'
                """
            )
        )
        await connection.execute(
            text(
                """
                ALTER TABLE prediction_evaluations
                ADD COLUMN IF NOT EXISTS evaluation_version VARCHAR(32)
                NOT NULL DEFAULT 'first_touch_v1'
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
                ALTER TABLE predictions
                ADD COLUMN IF NOT EXISTS take_profit_pct NUMERIC(20, 12)
                NOT NULL DEFAULT 0.60
                """
            )
        )
        await connection.execute(
            text(
                """
                ALTER TABLE predictions
                ADD COLUMN IF NOT EXISTS stop_loss_pct NUMERIC(20, 12)
                NOT NULL DEFAULT 0.30
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
                ALTER TABLE paper_positions
                ADD COLUMN IF NOT EXISTS model_name VARCHAR(64)
                NOT NULL DEFAULT 'unknown'
                """
            )
        )
        await connection.execute(
            text(
                """
                ALTER TABLE paper_positions
                ADD COLUMN IF NOT EXISTS gross_return_pct NUMERIC(20, 12)
                """
            )
        )
        await connection.execute(
            text(
                """
                ALTER TABLE paper_positions
                ADD COLUMN IF NOT EXISTS trading_cost_pct NUMERIC(20, 12)
                """
            )
        )
        await connection.execute(
            text(
                """
                UPDATE paper_positions AS pp
                SET model_name = p.model_name
                FROM predictions AS p
                WHERE pp.prediction_id = p.id
                  AND pp.model_name = 'unknown'
                """
            )
        )

        for column_name in (
            "spot_cvd_ratio_1m",
            "spot_cvd_ratio_5m",
            "spot_cvd_ratio_15m",
            "futures_cvd_ratio_1m",
            "futures_cvd_ratio_5m",
            "futures_cvd_ratio_15m",
        ):
            await connection.execute(
                text(
                    f"""
                    ALTER TABLE research_feature_snapshots
                    ADD COLUMN IF NOT EXISTS {column_name} NUMERIC(20, 16)
                    NOT NULL DEFAULT 0
                    """
                )
            )

        await connection.execute(
            text(
                """
                ALTER TABLE research_feature_snapshots
                ADD COLUMN IF NOT EXISTS liquidation_data_available BOOLEAN
                NOT NULL DEFAULT TRUE
                """
            )
        )
        await connection.execute(
            text(
                """
                ALTER TABLE research_feature_snapshots
                ADD COLUMN IF NOT EXISTS dataset_provenance VARCHAR(64)
                NOT NULL DEFAULT 'live_full'
                """
            )
        )

        for column_name, column_type in (
            ("trend_score_5m", "NUMERIC(20, 16)"),
            ("trend_score_15m", "NUMERIC(20, 16)"),
            ("trend_score_1h", "NUMERIC(20, 16)"),
            ("trend_score_4h", "NUMERIC(20, 16)"),
            ("atr_pct_5m", "NUMERIC(20, 12)"),
            ("atr_pct_15m", "NUMERIC(20, 12)"),
            ("atr_pct_1h", "NUMERIC(20, 12)"),
            ("atr_pct_4h", "NUMERIC(20, 12)"),
            ("trend_regime_5m", "VARCHAR(16)"),
            ("trend_regime_15m", "VARCHAR(16)"),
            ("trend_regime_1h", "VARCHAR(16)"),
            ("trend_regime_4h", "VARCHAR(16)"),
            ("volatility_regime_5m", "VARCHAR(16)"),
            ("volatility_regime_15m", "VARCHAR(16)"),
            ("volatility_regime_1h", "VARCHAR(16)"),
            ("volatility_regime_4h", "VARCHAR(16)"),
        ):
            await connection.execute(
                text(
                    f"""
                    ALTER TABLE research_feature_snapshots
                    ADD COLUMN IF NOT EXISTS {column_name} {column_type}
                    """
                )
            )

    if timescaledb_enabled:
        await _try_make_market_snapshots_hypertable(engine)
