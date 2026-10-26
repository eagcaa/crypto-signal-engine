"""On-demand Telegram coin analysis for the live market-watch worker."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from math import exp, log

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


def _usd(value: Decimal | float) -> str:
    amount = float(value)
    sign = "-" if amount < 0 else ""
    amount = abs(amount)
    if amount >= 1_000_000_000:
        return f"{sign}${amount / 1_000_000_000:.2f}B"
    if amount >= 1_000_000:
        return f"{sign}${amount / 1_000_000:.1f}M"
    if amount >= 1_000:
        return f"{sign}${amount / 1_000:.0f}K"
    return f"{sign}${amount:.0f}"


def _flow_comment(buy_share: float | None) -> str:
    if buy_share is None:
        return "yeterli işlem yok"
    if buy_share >= 55:
        return "agresif alım baskısı"
    if buy_share <= 45:
        return "agresif satım baskısı"
    return "dengeli"


def _candle_flow(
    candles,
    count: int,
) -> tuple[Decimal, Decimal, Decimal, Decimal, float | None]:
    selected = candles[-count:] if count > 0 else []

    buy_base = sum(
        (
            Decimal(str(getattr(candle, "taker_buy_base_volume", 0)))
            for candle in selected
        ),
        Decimal("0"),
    )
    total_base = sum(
        (Decimal(str(getattr(candle, "volume", 0))) for candle in selected),
        Decimal("0"),
    )
    sell_base = max(Decimal("0"), total_base - buy_base)

    buy_quote = sum(
        (
            Decimal(str(getattr(candle, "taker_buy_quote_volume", 0)))
            for candle in selected
        ),
        Decimal("0"),
    )
    total_quote = sum(
        (Decimal(str(getattr(candle, "quote_volume", 0))) for candle in selected),
        Decimal("0"),
    )
    sell_quote = max(Decimal("0"), total_quote - buy_quote)

    total = buy_base + sell_base
    buy_share = float(buy_base / total * Decimal("100")) if total > 0 else None
    return buy_base, sell_base, buy_quote, sell_quote, buy_share


def _flow_windows(minute_candles, five_minute_candles):
    specs = (
        ("15dk", minute_candles, 15),
        ("30dk", minute_candles, 30),
        ("1s", minute_candles, 60),
        ("2s", minute_candles, 120),
        ("4s", minute_candles, 240),
        ("1g", five_minute_candles, 288),
    )
    return [
        (label, *_candle_flow(candles, count))
        for label, candles, count in specs
    ]


def _flow_zones(
    minute_candles,
    current_price: float,
    *,
    lookback_minutes: int = 60,
    bucket_pct: float = 0.25,
    top_n: int = 3,
) -> tuple[list[tuple[float, float, Decimal]], list[tuple[float, float, Decimal]]]:
    if current_price <= 0:
        return [], []
    width = max(current_price * bucket_pct / 100.0, 1e-12)
    buckets: dict[int, list[Decimal]] = {}

    for candle in minute_candles[-lookback_minutes:]:
        representative = (
            float(candle.high) + float(candle.low) + float(candle.close)
        ) / 3.0
        bucket = int(representative // width)
        buy = Decimal(str(getattr(candle, "taker_buy_quote_volume", 0)))
        quote = Decimal(str(getattr(candle, "quote_volume", 0)))
        sell = max(Decimal("0"), quote - buy)
        totals = buckets.setdefault(bucket, [Decimal("0"), Decimal("0")])
        totals[0] += buy
        totals[1] += sell

    buyer_rows = []
    seller_rows = []
    for bucket, totals in buckets.items():
        low = bucket * width
        high = (bucket + 1) * width
        net = totals[0] - totals[1]
        if net > 0:
            buyer_rows.append((low, high, net))
        elif net < 0:
            seller_rows.append((low, high, -net))

    buyer_rows.sort(key=lambda row: row[2], reverse=True)
    seller_rows.sort(key=lambda row: row[2], reverse=True)
    return buyer_rows[:top_n], seller_rows[:top_n]


def _format_zone(low: float, high: float, amount: Decimal) -> str:
    return f"{_price(low)}–{_price(high)} ({_usd(amount)})"


def _coin_amount(value: Decimal, asset: str, current_price: float | None = None) -> str:
    amount = float(value)
    price = current_price or 0.0
    if price >= 1:
        decimals = 2
    elif price >= 0.1:
        decimals = 1
    else:
        decimals = 0

    if abs(amount) >= 100_000:
        decimals = 0
    elif abs(amount) >= 1_000:
        decimals = min(decimals, 1)

    rendered = f"{amount:,.{decimals}f}"
    return f"{rendered} {asset}"


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
        flow_enabled: bool = True,
    ) -> None:
        settings = get_settings()
        self._candles = candle_client or BinanceSpotCandleClient()
        self._derivatives = derivatives_client or BinanceDerivativesClient()
        self._bybit_derivatives = (
            bybit_derivatives_client or BybitDerivativesClient()
        )
        self._flow_enabled = flow_enabled
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
                # Calibrate on wicks: the forward check also uses highs/lows.
                extremes=[
                    (float(candle.low), float(candle.high)) for candle in candles
                ],
            )
            if estimate is not None:
                # Re-centre the band on the freshest price and start the clock
                # now. The last closed candle of a slow interval can be up to
                # one interval old (1h for the 24h band).
                radius = log(estimate.upper / points[-1].price)
                estimate = replace(
                    estimate,
                    lower=current_price * exp(-radius),
                    upper=current_price * exp(radius),
                )
                forecasts.append(
                    CoinRangeForecast(
                        horizon_minutes=horizon,
                        anchor_at=now,
                        anchor_price=current_price,
                        target_coverage=estimate.target_coverage,
                        lower_price=estimate.lower,
                        upper_price=estimate.upper,
                        calibration_count=estimate.calibration_count,
                        volatility_scaled=estimate.volatility_scaled,
                    )
                )
            ranges.append((horizon, estimate))

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

        if self._flow_enabled:
            minute_candles = candle_sets[0]
            five_minute_candles = candle_sets[1]
            base_asset = symbol.removesuffix("USDT")
            lines.extend(
                [
                    "",
                    "Agresif taker akışı (Binance Spot mum verisi)",
                    "Not: Binance uygulamasındaki Money Flow ile aynı metrik değildir.",
                ]
            )
            for (
                label,
                buy_base,
                sell_base,
                buy_quote,
                sell_quote,
                buy_share,
            ) in _flow_windows(minute_candles, five_minute_candles):
                total = buy_base + sell_base
                if total <= 0:
                    lines.append(f"{label}: veri yok")
                    continue
                net_base = buy_base - sell_base
                net_quote = buy_quote - sell_quote
                lines.append(
                    f"{label}: net {_coin_amount(net_base, base_asset, current_price)} "
                    f"({_usd(net_quote)}) · alım %{buy_share:.0f} · "
                    f"{_flow_comment(buy_share)}"
                )
                if label == "15dk":
                    lines.append(
                        "  Detay: agresif alım "
                        f"{_coin_amount(buy_base, base_asset, current_price)} "
                        f"({_usd(buy_quote)}) · agresif satım "
                        f"{_coin_amount(sell_base, base_asset, current_price)} "
                        f"({_usd(sell_quote)})"
                    )

            buy_zones, sell_zones = _flow_zones(minute_candles, current_price)
            lines.extend(
                [
                    "",
                    "Son 1s yoğun fiyat bölgeleri (1dk mum bazlı yaklaşık)",
                ]
            )
            if buy_zones:
                lines.append(
                    "Net alıcı bölge: "
                    + " | ".join(
                        _format_zone(low, high, amount)
                        for low, high, amount in buy_zones
                    )
                )
            else:
                lines.append(
                    "Net alıcı bölge: yok (son 1 saatte her bölgede satış ağır)"
                )
            if sell_zones:
                lines.append(
                    "Net satıcı bölge: "
                    + " | ".join(
                        _format_zone(low, high, amount)
                        for low, high, amount in sell_zones
                    )
                )
            else:
                lines.append(
                    "Net satıcı bölge: yok (son 1 saatte her bölgede alım ağır)"
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
        return CoinAnalysis(
            symbol=symbol,
            generated_at=now,
            text="\n".join(lines),
            forecasts=tuple(forecasts),
        )

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
