from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, Uuid
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class MarketSnapshotRow(Base):
    __tablename__ = "market_snapshots"

    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        primary_key=True,
    )
    symbol: Mapped[str] = mapped_column(
        String(32),
        primary_key=True,
    )
    price: Mapped[Decimal | None] = mapped_column(Numeric(30, 12))

    binance_spot_cvd: Mapped[Decimal | None] = mapped_column(Numeric(30, 12))
    binance_futures_cvd: Mapped[Decimal | None] = mapped_column(Numeric(30, 12))
    bybit_spot_cvd: Mapped[Decimal | None] = mapped_column(Numeric(30, 12))
    bybit_futures_cvd: Mapped[Decimal | None] = mapped_column(Numeric(30, 12))

    spot_cvd_total: Mapped[Decimal | None] = mapped_column(Numeric(30, 12))
    futures_cvd_total: Mapped[Decimal | None] = mapped_column(Numeric(30, 12))
    spot_futures_divergence: Mapped[Decimal | None] = mapped_column(Numeric(30, 12))

    binance_book_imbalance: Mapped[Decimal | None] = mapped_column(Numeric(20, 16))
    bybit_book_imbalance: Mapped[Decimal | None] = mapped_column(Numeric(20, 16))
    cross_exchange_book_divergence: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 16)
    )

    buy_pressure: Mapped[Decimal | None] = mapped_column(Numeric(20, 16))
    sell_pressure: Mapped[Decimal | None] = mapped_column(Numeric(20, 16))

    binance_spot_age_ms: Mapped[int | None] = mapped_column(Integer)
    binance_futures_age_ms: Mapped[int | None] = mapped_column(Integer)
    bybit_spot_age_ms: Mapped[int | None] = mapped_column(Integer)
    bybit_futures_age_ms: Mapped[int | None] = mapped_column(Integer)
    binance_book_age_ms: Mapped[int | None] = mapped_column(Integer)
    bybit_book_age_ms: Mapped[int | None] = mapped_column(Integer)

    data_quality: Mapped[Decimal] = mapped_column(
        Numeric(8, 6),
        nullable=False,
    )


class PredictionRow(Base):
    __tablename__ = "predictions"

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
    )
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    horizon_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    entry_price: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    raw_score: Mapped[Decimal] = mapped_column(Numeric(20, 16), nullable=False)
    data_quality: Mapped[Decimal] = mapped_column(Numeric(8, 6), nullable=False)
    model_name: Mapped[str] = mapped_column(String(64), nullable=False)


class PredictionEvaluationRow(Base):
    __tablename__ = "prediction_evaluations"

    prediction_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("predictions.id", ondelete="CASCADE"),
        primary_key=True,
    )
    evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    exit_price: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    return_pct: Mapped[Decimal] = mapped_column(Numeric(20, 12), nullable=False)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
