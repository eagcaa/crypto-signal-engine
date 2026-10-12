"""On-demand Telegram coin analysis for the live market-watch worker."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from crypto_signal_engine.collectors.binance import (
    BinanceDerivativesClient,
    BinanceSpotCandleClient,
)
from crypto_signal_engine.collectors.bybit import BybitDerivativesClient
from crypto_signal_engine.config.settings import get_settings
from crypto_signal_engine.forecast.range import PricePoint, estimate_live_range


def normalize_symbol(text: str) -> str:
    token = text.strip().upper()
    if token.startswith("/ANALYZE"):
        token = token.removeprefix("/ANALYZE")
    token = token.split("@", 1)[0]
    token = token.replace("/", "").replace("-", "").replace(" ", "")
    if not token:
        raise ValueError("coin symbol is required")
    if token.endswith("USDT"):
        return token
    return f"{token}USDT"


def _price(value: float) -> str:
    if value >= 1_000:
        return f"{value:,.1f}"
    if value >= 1:
        return f"{value:,.4f}"
    return f"{value:.6f}"


def _pct(value: Decimal | float | None) -> str:
    if value is None:
        return "n/a"
    return f"{float(value):+.2f}%"


@dataclass(frozen=True, slots=True)
class CoinRangeForecast:
    horizon_minutes: int
    anchor_at: datetime
    anchor_price: float
    target_coverage: float
    lower_price: float
    upper_price: float
    calibration_count: int
    volatility_scaled: bool


@dataclass(frozen=True, slots=True)
class CoinAnalysis:
    symbol: str
    generated_at: datetime
    text: str
    forecasts: tuple[CoinRangeForecast, ...] = ()


class CoinAnalyzer:
    """Fetch fresh public Binance/Bybit data and return a direction-agnostic analysis."""

    def __init__(
        self,
        *,
        candle_client: BinanceSpotCandleClient | None = None,
        derivatives_client: BinanceDerivativesClient | None = None,
        bybit_derivatives_client: BybitDerivativesClient | None = None,
        bybit_enabled: bool | None = None,
    ) -> None:
        settings = get_settings()
        self._candles = candle_client or BinanceSpotCandleClient()
        self._derivatives = derivatives_client or BinanceDerivativesClient()
        self._bybit_derivatives = (
            bybit_derivatives_client or BybitDerivativesClient()
        )
        self._bybit_enabled = (
            settings.bybit_enabled if bybit_enabled is None else bybit_enabled
        )

    async def analyze(self, requested: str) -> CoinAnalysis:
        symbol = normalize_symbol(requested)

        specs = (
            (15, "1m", 1000),
            (60, "5m", 1000),
            (240, "15m", 1000),
            (1440, "1h", 1000),
        )
        candle_tasks = [
            self._candles.fetch_closed(symbol, interval=interval, limit=limit)
            for _, interval, limit in specs
        ]
        derivative_task = asyncio.create_task(self._safe_derivatives(symbol))
        bybit_task = (
            asyncio.create_task(self._safe_bybit_derivatives(symbol))
            if self._bybit_enabled
            else None
        )
        candle_sets = await asyncio.gather(*candle_tasks)
        derivatives = await derivative_task
        bybit_derivatives = await bybit_task if bybit_task is not None else None

        if not candle_sets or not candle_sets[0]:
            raise RuntimeError(f"{symbol} için Binance spot verisi bulunamadı")

        current_price = float(candle_sets[0][-1].close)
        now = datetime.now(UTC)
        ranges: list[tuple[int, object | None]] = []
        forecasts: list[CoinRangeForecast] = []
        for (horizon, _, _), candles in zip(specs, candle_sets, strict=True):
            points = [
                PricePoint(timestamp=candle.close_time, price=float(candle.close))
                for candle in candles
            ]
            estimate = estimate_live_range(
                points,
                horizon_minutes=horizon,
                target_coverage=0.70,
                minimum_calibration_count=20,
                volatility_scaled=True,
            )
            ranges.append((horizon, estimate))
            if estimate is not None:
                anchor = candles[-1]
                forecasts.append(
                    CoinRangeForecast(
                        horizon_minutes=horizon,
                        anchor_at=anchor.close_time,
                        anchor_price=float(anchor.close),
                        target_coverage=estimate.target_coverage,
                        lower_price=estimate.lower,
                        upper_price=estimate.upper,
                        calibration_count=estimate.calibration_count,
                        volatility_scaled=estimate.volatility_scaled,
                    )
                )

        hour_candles = candle_sets[-1]
        recent_24h = hour_candles[-24:]
        high_24h = max(float(item.high) for item in recent_24h)
        low_24h = min(float(item.low) for item in recent_24h)

        lines = [
            f"🔎 {symbol} — {_price(current_price)}",
            "",
            "Deneysel hareket aralığı",
            "(hedef kapsama %70; canlı forward doğrulama sürüyor)",
        ]
        labels = {15: "15 dk", 60: "1 saat", 240: "4 saat", 1440: "24 saat"}
        for horizon, estimate in ranges:
            if estimate is None:
                lines.append(f"{labels[horizon]}: yeterli kalibrasyon yok")
            else:
                lines.append(
                    f"{labels[horizon]}: {_price(estimate.lower)} – "
                    f"{_price(estimate.upper)} "
                    f"(n={estimate.calibration_count})"
                )

        lines.extend(
            [
                "",
                "Yakın piyasa yapısı",
                f"24s yüksek: {_price(high_24h)}",
                f"24s düşük: {_price(low_24h)}",
            ]
        )

        lines.extend(["", "Vadeli piyasa"])

        if derivatives is not None:
            funding = (
                "n/a"
                if derivatives.funding_rate is None
                else f"{float(derivatives.funding_rate) * 100:.4f}%"
            )
            lines.extend(
                [
                    "Binance:",
                    f"  OI 5 dk: {_pct(derivatives.oi_change_5m_pct)}",
                    f"  OI 15 dk: {_pct(derivatives.oi_change_15m_pct)}",
                    f"  Funding: {funding}",
                    (
                        "  Long/short: "
                        + (
                            "n/a"
                            if derivatives.long_short_ratio is None
                            else f"{float(derivatives.long_short_ratio):.2f}"
                        )
                    ),
                ]
            )
        else:
            lines.append("Binance: veri alınamadı / mevcut değil")

        if self._bybit_enabled:
            if bybit_derivatives is not None:
                bybit_funding = (
                    "n/a"
                    if bybit_derivatives.funding_rate is None
                    else f"{float(bybit_derivatives.funding_rate) * 100:.4f}%"
                )
                lines.extend(
                    [
                        "Bybit:",
                        f"  OI 5 dk: {_pct(bybit_derivatives.oi_change_5m_pct)}",
                        f"  OI 15 dk: {_pct(bybit_derivatives.oi_change_15m_pct)}",
                        f"  Funding: {bybit_funding}",
                        (
                            "  Long/short: "
                            + (
                                "n/a"
                                if bybit_derivatives.long_short_ratio is None
                                else f"{float(bybit_derivatives.long_short_ratio):.2f}"
                            )
                        ),
                    ]
                )
            else:
                lines.append("Bybit: veri alınamadı / mevcut değil")

        lines.extend(
            [
                "",
                "Bu bir yön tahmini veya al/sat sinyali değildir.",
                f"Güncelleme: {now.strftime('%H:%M UTC')}",
            ]
        )
        return CoinAnalysis(symbol=symbol, text="\n".join(lines))

    async def _safe_derivatives(self, symbol: str):
        try:
            return await self._derivatives.fetch_snapshot(symbol)
        except Exception:
            return None

    async def _safe_bybit_derivatives(self, symbol: str):
        try:
            return await self._bybit_derivatives.fetch_snapshot(symbol)
        except Exception:
            return None
