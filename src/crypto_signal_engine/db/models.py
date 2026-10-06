from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Integer, Numeric, String
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
