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
    take_profit_pct: Mapped[Decimal] = mapped_column(
        Numeric(20, 12),
        nullable=False,
        default=Decimal("0.60"),
    )
    stop_loss_pct: Mapped[Decimal] = mapped_column(
        Numeric(20, 12),
        nullable=False,
        default=Decimal("0.30"),
    )
    model_name: Mapped[str] = mapped_column(String(64), nullable=False)
    feature_contributions_json: Mapped[str | None] = mapped_column(String(4096))
    reason: Mapped[str | None] = mapped_column(String(2048))


class PredictionEvaluationRow(Base):
    __tablename__ = "prediction_evaluations"

    prediction_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("predictions.id", ondelete="CASCADE"),
        primary_key=True,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="evaluated",
    )
    outcome: Mapped[str | None] = mapped_column(String(32))
    label: Mapped[int | None] = mapped_column(Integer)
    evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    exit_price: Mapped[Decimal | None] = mapped_column(Numeric(30, 12))
    gross_return_pct: Mapped[Decimal | None] = mapped_column(Numeric(20, 12))
    trading_cost_pct: Mapped[Decimal | None] = mapped_column(Numeric(20, 12))
    return_pct: Mapped[Decimal | None] = mapped_column(Numeric(20, 12))
    success: Mapped[bool | None] = mapped_column(Boolean)
    evaluation_source: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="unknown",
    )
    evaluation_version: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="first_touch_v1",
    )


class CoinGlassSnapshotRow(Base):
    __tablename__ = "coinglass_snapshots"

    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        primary_key=True,
    )
    symbol: Mapped[str] = mapped_column(
        String(32),
        primary_key=True,
    )
    open_interest_usd: Mapped[Decimal | None] = mapped_column(Numeric(30, 6))
    oi_change_5m_pct: Mapped[Decimal | None] = mapped_column(Numeric(20, 10))
    oi_change_15m_pct: Mapped[Decimal | None] = mapped_column(Numeric(20, 10))
    funding_rate_binance: Mapped[Decimal | None] = mapped_column(Numeric(20, 12))
    funding_rate_bybit: Mapped[Decimal | None] = mapped_column(Numeric(20, 12))
    taker_buy_ratio: Mapped[Decimal | None] = mapped_column(Numeric(20, 10))
    taker_sell_ratio: Mapped[Decimal | None] = mapped_column(Numeric(20, 10))
    taker_buy_volume_usd: Mapped[Decimal | None] = mapped_column(Numeric(30, 6))
    taker_sell_volume_usd: Mapped[Decimal | None] = mapped_column(Numeric(30, 6))


class ExchangeDerivativesSnapshotRow(Base):
    __tablename__ = "exchange_derivatives_snapshots"

    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        primary_key=True,
    )
    exchange: Mapped[str] = mapped_column(
        String(16),
        primary_key=True,
    )
    symbol: Mapped[str] = mapped_column(
        String(32),
        primary_key=True,
    )
    open_interest: Mapped[Decimal | None] = mapped_column(Numeric(30, 12))
    open_interest_value_usd: Mapped[Decimal | None] = mapped_column(Numeric(30, 6))
    oi_change_5m_pct: Mapped[Decimal | None] = mapped_column(Numeric(20, 10))
    oi_change_15m_pct: Mapped[Decimal | None] = mapped_column(Numeric(20, 10))
    funding_rate: Mapped[Decimal | None] = mapped_column(Numeric(20, 12))
    long_short_ratio: Mapped[Decimal | None] = mapped_column(Numeric(20, 10))
    long_account_ratio: Mapped[Decimal | None] = mapped_column(Numeric(20, 10))
    short_account_ratio: Mapped[Decimal | None] = mapped_column(Numeric(20, 10))
    top_trader_long_short_ratio: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 10)
    )
    taker_buy_sell_ratio: Mapped[Decimal | None] = mapped_column(Numeric(20, 10))
    taker_buy_volume: Mapped[Decimal | None] = mapped_column(Numeric(30, 12))
    taker_sell_volume: Mapped[Decimal | None] = mapped_column(Numeric(30, 12))


class LiquidationEventRow(Base):
    __tablename__ = "liquidation_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    exchange: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    event_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    position_side: Mapped[str] = mapped_column(String(16), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    notional_usd: Mapped[Decimal] = mapped_column(Numeric(30, 6), nullable=False)


class ResearchFeatureSnapshotRow(Base):
    __tablename__ = "research_feature_snapshots"

    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        primary_key=True,
    )
    symbol: Mapped[str] = mapped_column(
        String(32),
        primary_key=True,
    )
    price: Mapped[Decimal | None] = mapped_column(Numeric(30, 12))

    spot_cvd_1m: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    spot_cvd_5m: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    spot_cvd_15m: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    futures_cvd_1m: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    futures_cvd_5m: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    futures_cvd_15m: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    spot_cvd_ratio_1m: Mapped[Decimal] = mapped_column(Numeric(20, 16), nullable=False)
    spot_cvd_ratio_5m: Mapped[Decimal] = mapped_column(Numeric(20, 16), nullable=False)
    spot_cvd_ratio_15m: Mapped[Decimal] = mapped_column(Numeric(20, 16), nullable=False)
    futures_cvd_ratio_1m: Mapped[Decimal] = mapped_column(Numeric(20, 16), nullable=False)
    futures_cvd_ratio_5m: Mapped[Decimal] = mapped_column(Numeric(20, 16), nullable=False)
    futures_cvd_ratio_15m: Mapped[Decimal] = mapped_column(Numeric(20, 16), nullable=False)
    spot_trade_sources: Mapped[int] = mapped_column(Integer, nullable=False)
    futures_trade_sources: Mapped[int] = mapped_column(Integer, nullable=False)
    history_seconds: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    trend_score_5m: Mapped[Decimal | None] = mapped_column(Numeric(20, 16))
    trend_score_15m: Mapped[Decimal | None] = mapped_column(Numeric(20, 16))
    trend_score_1h: Mapped[Decimal | None] = mapped_column(Numeric(20, 16))
    trend_score_4h: Mapped[Decimal | None] = mapped_column(Numeric(20, 16))
    atr_pct_5m: Mapped[Decimal | None] = mapped_column(Numeric(20, 12))
    atr_pct_15m: Mapped[Decimal | None] = mapped_column(Numeric(20, 12))
    atr_pct_1h: Mapped[Decimal | None] = mapped_column(Numeric(20, 12))
    atr_pct_4h: Mapped[Decimal | None] = mapped_column(Numeric(20, 12))
    trend_regime_5m: Mapped[str | None] = mapped_column(String(16))
    trend_regime_15m: Mapped[str | None] = mapped_column(String(16))
    trend_regime_1h: Mapped[str | None] = mapped_column(String(16))
    trend_regime_4h: Mapped[str | None] = mapped_column(String(16))
    volatility_regime_5m: Mapped[str | None] = mapped_column(String(16))
    volatility_regime_15m: Mapped[str | None] = mapped_column(String(16))
    volatility_regime_1h: Mapped[str | None] = mapped_column(String(16))
    volatility_regime_4h: Mapped[str | None] = mapped_column(String(16))

    binance_oi_change_5m_pct: Mapped[Decimal | None] = mapped_column(Numeric(20, 10))
    binance_oi_change_15m_pct: Mapped[Decimal | None] = mapped_column(Numeric(20, 10))
    bybit_oi_change_5m_pct: Mapped[Decimal | None] = mapped_column(Numeric(20, 10))
    bybit_oi_change_15m_pct: Mapped[Decimal | None] = mapped_column(Numeric(20, 10))
    binance_funding_rate: Mapped[Decimal | None] = mapped_column(Numeric(20, 12))
    bybit_funding_rate: Mapped[Decimal | None] = mapped_column(Numeric(20, 12))
    binance_long_short_ratio: Mapped[Decimal | None] = mapped_column(Numeric(20, 10))
    bybit_long_short_ratio: Mapped[Decimal | None] = mapped_column(Numeric(20, 10))
    binance_top_trader_long_short_ratio: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 10)
    )
    binance_taker_buy_sell_ratio: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 10)
    )

    long_liquidations_5m_usd: Mapped[Decimal] = mapped_column(
        Numeric(30, 6),
        nullable=False,
    )
    short_liquidations_5m_usd: Mapped[Decimal] = mapped_column(
        Numeric(30, 6),
        nullable=False,
    )
    liquidation_imbalance_5m: Mapped[Decimal] = mapped_column(
        Numeric(20, 12),
        nullable=False,
    )
    long_liquidations_15m_usd: Mapped[Decimal] = mapped_column(
        Numeric(30, 6),
        nullable=False,
    )
    short_liquidations_15m_usd: Mapped[Decimal] = mapped_column(
        Numeric(30, 6),
        nullable=False,
    )
    liquidation_imbalance_15m: Mapped[Decimal] = mapped_column(
        Numeric(20, 12),
        nullable=False,
    )

    binance_book_imbalance: Mapped[Decimal | None] = mapped_column(Numeric(20, 16))
    bybit_book_imbalance: Mapped[Decimal | None] = mapped_column(Numeric(20, 16))
    market_data_quality: Mapped[Decimal] = mapped_column(Numeric(8, 6), nullable=False)
    liquidation_data_available: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )
    dataset_provenance: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="live_full",
    )



class PaperPositionRow(Base):
    __tablename__ = "paper_positions"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    prediction_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("predictions.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    horizon_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    entry_price: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    notional: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    model_name: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="unknown",
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    exit_price: Mapped[Decimal | None] = mapped_column(Numeric(30, 12))
    gross_return_pct: Mapped[Decimal | None] = mapped_column(Numeric(20, 12))
    trading_cost_pct: Mapped[Decimal | None] = mapped_column(Numeric(20, 12))
    return_pct: Mapped[Decimal | None] = mapped_column(Numeric(20, 12))
    pnl: Mapped[Decimal | None] = mapped_column(Numeric(30, 12))
    close_reason: Mapped[str | None] = mapped_column(String(64))
