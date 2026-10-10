import asyncio
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from crypto_signal_engine.domain.derivatives import (
    ExchangeDerivativesSnapshot,
    LiquidatedPositionSide,
    LiquidationEvent,
)
from crypto_signal_engine.domain.models import Exchange, MarketType, TradeSide, TradeTick
from crypto_signal_engine.features.technical import TechnicalFeatureSnapshot
from crypto_signal_engine.market import MarketSnapshot


@dataclass(frozen=True, slots=True)
class ResearchFeatureSnapshot:
    symbol: str
    timestamp: datetime
    price: Decimal | None

    spot_cvd_1m: Decimal
    spot_cvd_5m: Decimal
    spot_cvd_15m: Decimal
    futures_cvd_1m: Decimal
    futures_cvd_5m: Decimal
    futures_cvd_15m: Decimal
    spot_cvd_ratio_1m: Decimal
    spot_cvd_ratio_5m: Decimal
    spot_cvd_ratio_15m: Decimal
    futures_cvd_ratio_1m: Decimal
    futures_cvd_ratio_5m: Decimal
    futures_cvd_ratio_15m: Decimal
    spot_trade_sources: int
    futures_trade_sources: int
    history_seconds: int

    trend_score_5m: Decimal | None
    trend_score_15m: Decimal | None
    trend_score_1h: Decimal | None
    trend_score_4h: Decimal | None
    atr_pct_5m: Decimal | None
    atr_pct_15m: Decimal | None
    atr_pct_1h: Decimal | None
    atr_pct_4h: Decimal | None
    trend_regime_5m: str | None
    trend_regime_15m: str | None
    trend_regime_1h: str | None
    trend_regime_4h: str | None
    volatility_regime_5m: str | None
    volatility_regime_15m: str | None
    volatility_regime_1h: str | None
    volatility_regime_4h: str | None

    binance_oi_change_5m_pct: Decimal | None
    binance_oi_change_15m_pct: Decimal | None
    bybit_oi_change_5m_pct: Decimal | None
    bybit_oi_change_15m_pct: Decimal | None
    binance_funding_rate: Decimal | None
    bybit_funding_rate: Decimal | None
    binance_long_short_ratio: Decimal | None
    bybit_long_short_ratio: Decimal | None
    binance_top_trader_long_short_ratio: Decimal | None
    binance_taker_buy_sell_ratio: Decimal | None

    long_liquidations_5m_usd: Decimal
    short_liquidations_5m_usd: Decimal
    liquidation_imbalance_5m: Decimal
    long_liquidations_15m_usd: Decimal
    short_liquidations_15m_usd: Decimal
    liquidation_imbalance_15m: Decimal

    binance_book_imbalance: Decimal | None
    bybit_book_imbalance: Decimal | None
    market_data_quality: Decimal
    liquidation_data_available: bool = True
    dataset_provenance: str = "live_full"


@dataclass(frozen=True, slots=True)
class _SignedTrade:
    event_time: datetime
    value: Decimal


class ResearchFeatureAggregator:
    _MAX_WINDOW = timedelta(minutes=15)

    def __init__(self, symbol: str) -> None:
        self.symbol = symbol.upper()
        self._trades: dict[tuple[Exchange, MarketType], deque[_SignedTrade]] = {}
        self._derivatives: dict[Exchange, ExchangeDerivativesSnapshot] = {}
        self._technicals: dict[str, TechnicalFeatureSnapshot] = {}
        self._liquidations: deque[LiquidationEvent] = deque()
        self._first_trade_time: datetime | None = None
        self._lock = asyncio.Lock()

    async def update_trade(self, trade: TradeTick) -> None:
        if trade.symbol.upper() != self.symbol:
            return

        signed_quantity = (
            trade.quantity
            if trade.side == TradeSide.BUY
            else -trade.quantity
        )

        async with self._lock:
            if (
                self._first_trade_time is None
                or trade.event_time < self._first_trade_time
            ):
                self._first_trade_time = trade.event_time

            key = (trade.exchange, trade.market_type)
            queue = self._trades.setdefault(key, deque())
            queue.append(_SignedTrade(trade.event_time, signed_quantity))
            self._trim_trades(trade.event_time)

    async def update_derivatives(
        self,
        snapshot: ExchangeDerivativesSnapshot,
    ) -> None:
        if snapshot.symbol.upper() != self.symbol:
            return
        async with self._lock:
            self._derivatives[snapshot.exchange] = snapshot

    async def update_technical(
        self,
        snapshot: TechnicalFeatureSnapshot,
    ) -> None:
        if snapshot.interval not in {"5m", "15m", "1h", "4h"}:
            return
        async with self._lock:
            self._technicals[snapshot.interval] = snapshot

    async def update_liquidation(self, event: LiquidationEvent) -> None:
        if event.symbol.upper() != self.symbol:
            return
        async with self._lock:
            self._liquidations.append(event)
            self._trim_liquidations(event.event_time)

    async def snapshot(
        self,
        market_snapshot: MarketSnapshot,
    ) -> ResearchFeatureSnapshot:
        async with self._lock:
            now = market_snapshot.timestamp
            self._trim_trades(now)
            self._trim_liquidations(now)

            binance = self._derivatives.get(Exchange.BINANCE)
            if binance is not None and binance.timestamp > now:
                binance = None
            bybit = self._derivatives.get(Exchange.BYBIT)
            if bybit is not None and bybit.timestamp > now:
                bybit = None
            technical_5m = self._technicals.get("5m")
            technical_15m = self._technicals.get("15m")
            technical_1h = self._technicals.get("1h")
            technical_4h = self._technicals.get("4h")
            long_5m, short_5m = self._liquidation_totals(now, timedelta(minutes=5))
            long_15m, short_15m = self._liquidation_totals(now, timedelta(minutes=15))

            return ResearchFeatureSnapshot(
                symbol=self.symbol,
                timestamp=now,
                price=market_snapshot.price,
                spot_cvd_1m=self._cvd_window(now, MarketType.SPOT, timedelta(minutes=1)),
                spot_cvd_5m=self._cvd_window(now, MarketType.SPOT, timedelta(minutes=5)),
                spot_cvd_15m=self._cvd_window(now, MarketType.SPOT, timedelta(minutes=15)),
                futures_cvd_1m=self._cvd_window(now, MarketType.FUTURES, timedelta(minutes=1)),
                futures_cvd_5m=self._cvd_window(now, MarketType.FUTURES, timedelta(minutes=5)),
                futures_cvd_15m=self._cvd_window(now, MarketType.FUTURES, timedelta(minutes=15)),
                spot_cvd_ratio_1m=self._cvd_ratio_window(
                    now, MarketType.SPOT, timedelta(minutes=1)
                ),
                spot_cvd_ratio_5m=self._cvd_ratio_window(
                    now, MarketType.SPOT, timedelta(minutes=5)
                ),
                spot_cvd_ratio_15m=self._cvd_ratio_window(
                    now, MarketType.SPOT, timedelta(minutes=15)
                ),
                futures_cvd_ratio_1m=self._cvd_ratio_window(
                    now, MarketType.FUTURES, timedelta(minutes=1)
                ),
                futures_cvd_ratio_5m=self._cvd_ratio_window(
                    now, MarketType.FUTURES, timedelta(minutes=5)
                ),
                futures_cvd_ratio_15m=self._cvd_ratio_window(
                    now, MarketType.FUTURES, timedelta(minutes=15)
                ),
                spot_trade_sources=self._source_count(MarketType.SPOT),
                futures_trade_sources=self._source_count(MarketType.FUTURES),
                history_seconds=self._history_seconds(now),
                trend_score_5m=(
                    technical_5m.trend_score if technical_5m else None
                ),
                trend_score_15m=(
                    technical_15m.trend_score if technical_15m else None
                ),
                trend_score_1h=technical_1h.trend_score if technical_1h else None,
                trend_score_4h=technical_4h.trend_score if technical_4h else None,
                atr_pct_5m=technical_5m.atr_pct if technical_5m else None,
                atr_pct_15m=technical_15m.atr_pct if technical_15m else None,
                atr_pct_1h=technical_1h.atr_pct if technical_1h else None,
                atr_pct_4h=technical_4h.atr_pct if technical_4h else None,
                trend_regime_5m=(
                    technical_5m.trend_regime if technical_5m else None
                ),
                trend_regime_15m=(
                    technical_15m.trend_regime if technical_15m else None
                ),
                trend_regime_1h=technical_1h.trend_regime if technical_1h else None,
                trend_regime_4h=technical_4h.trend_regime if technical_4h else None,
                volatility_regime_5m=(
                    technical_5m.volatility_regime if technical_5m else None
                ),
                volatility_regime_15m=(
                    technical_15m.volatility_regime if technical_15m else None
                ),
                volatility_regime_1h=(
                    technical_1h.volatility_regime if technical_1h else None
                ),
                volatility_regime_4h=(
                    technical_4h.volatility_regime if technical_4h else None
                ),
                binance_oi_change_5m_pct=binance.oi_change_5m_pct if binance else None,
                binance_oi_change_15m_pct=binance.oi_change_15m_pct if binance else None,
                bybit_oi_change_5m_pct=bybit.oi_change_5m_pct if bybit else None,
                bybit_oi_change_15m_pct=bybit.oi_change_15m_pct if bybit else None,
                binance_funding_rate=binance.funding_rate if binance else None,
                bybit_funding_rate=bybit.funding_rate if bybit else None,
                binance_long_short_ratio=binance.long_short_ratio if binance else None,
                bybit_long_short_ratio=bybit.long_short_ratio if bybit else None,
                binance_top_trader_long_short_ratio=(
                    binance.top_trader_long_short_ratio if binance else None
                ),
                binance_taker_buy_sell_ratio=(
                    binance.taker_buy_sell_ratio if binance else None
                ),
                long_liquidations_5m_usd=long_5m,
                short_liquidations_5m_usd=short_5m,
                liquidation_imbalance_5m=self._liquidation_imbalance(long_5m, short_5m),
                long_liquidations_15m_usd=long_15m,
                short_liquidations_15m_usd=short_15m,
                liquidation_imbalance_15m=self._liquidation_imbalance(long_15m, short_15m),
                binance_book_imbalance=market_snapshot.binance_book_imbalance,
                bybit_book_imbalance=market_snapshot.bybit_book_imbalance,
                market_data_quality=market_snapshot.data_quality,
                liquidation_data_available=True,
                dataset_provenance="live_full",
            )

    def _history_seconds(self, now: datetime) -> int:
        if self._first_trade_time is None:
            return 0
        return max(
            0,
            int((now - self._first_trade_time).total_seconds()),
        )

    def _cvd_window(
        self,
        now: datetime,
        market_type: MarketType,
        window: timedelta,
    ) -> Decimal:
        cutoff = now - window
        total = Decimal("0")
        for (_, current_market_type), queue in self._trades.items():
            if current_market_type != market_type:
                continue
            total += sum(
                (item.value for item in queue if cutoff <= item.event_time <= now),
                Decimal("0"),
            )
        return total

    def _cvd_ratio_window(
        self,
        now: datetime,
        market_type: MarketType,
        window: timedelta,
    ) -> Decimal:
        cutoff = now - window
        signed_total = Decimal("0")
        absolute_total = Decimal("0")

        for (_, current_market_type), queue in self._trades.items():
            if current_market_type != market_type:
                continue

            for item in queue:
                if cutoff <= item.event_time <= now:
                    signed_total += item.value
                    absolute_total += abs(item.value)

        if absolute_total == 0:
            return Decimal("0")

        return signed_total / absolute_total

    def _source_count(self, market_type: MarketType) -> int:
        return sum(
            1
            for (_, current_market_type), queue in self._trades.items()
            if current_market_type == market_type and queue
        )

    def _liquidation_totals(
        self,
        now: datetime,
        window: timedelta,
    ) -> tuple[Decimal, Decimal]:
        cutoff = now - window
        long_total = Decimal("0")
        short_total = Decimal("0")
        for event in self._liquidations:
            if not (cutoff <= event.event_time <= now):
                continue
            if event.position_side == LiquidatedPositionSide.LONG:
                long_total += event.notional_usd
            else:
                short_total += event.notional_usd
        return long_total, short_total

    @staticmethod
    def _liquidation_imbalance(
        long_total: Decimal,
        short_total: Decimal,
    ) -> Decimal:
        total = long_total + short_total
        if total == 0:
            return Decimal("0")
        return (short_total - long_total) / total

    def _trim_trades(self, now: datetime) -> None:
        cutoff = now - self._MAX_WINDOW
        for queue in self._trades.values():
            while queue and queue[0].event_time < cutoff:
                queue.popleft()

    def _trim_liquidations(self, now: datetime) -> None:
        cutoff = now - self._MAX_WINDOW
        while self._liquidations and self._liquidations[0].event_time < cutoff:
            self._liquidations.popleft()
