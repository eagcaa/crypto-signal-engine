"""Market-watch alerts: describe what the market is doing right now.

This module never predicts direction and never trades. It turns live
trades, liquidations and derivatives snapshots into short Turkish
descriptions that a human trader can use as context.
"""

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from crypto_signal_engine.domain.derivatives import (
    ExchangeDerivativesSnapshot,
    LiquidatedPositionSide,
    LiquidationEvent,
)
from crypto_signal_engine.domain.models import MarketType, TradeSide, TradeTick


@dataclass(frozen=True, slots=True)
class WatchConfig:
    whale_trade_usd: float = 1_000_000.0
    window: timedelta = timedelta(minutes=15)
    price_move_pct: float = 0.5
    oi_move_pct: float = 1.0
    flow_min_usd: float = 5_000_000.0
    liquidation_window: timedelta = timedelta(minutes=5)
    liquidation_wave_usd: float = 5_000_000.0
    funding_high: float = 0.0005
    funding_low: float = -0.0001
    cooldown: timedelta = timedelta(minutes=30)
    summary_every: timedelta = timedelta(hours=1)


@dataclass(frozen=True, slots=True)
class WatchAlert:
    kind: str
    timestamp: datetime
    text: str


def _usd(value: float) -> str:
    sign = "-" if value < 0 else ""
    value = abs(value)
    if value >= 1_000_000_000:
        return f"{sign}${value / 1_000_000_000:.2f}B"
    if value >= 1_000_000:
        return f"{sign}${value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{sign}${value / 1_000:.0f}K"
    return f"{sign}${value:.0f}"


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:+.2f}%"


@dataclass(slots=True)
class _Window:
    span: timedelta
    items: deque = field(default_factory=deque)

    def add(self, timestamp: datetime, value: float) -> None:
        self.items.append((timestamp, value))
        self.trim(timestamp)

    def trim(self, now: datetime) -> None:
        cutoff = now - self.span
        while self.items and self.items[0][0] < cutoff:
            self.items.popleft()

    def total(self, now: datetime) -> float:
        self.trim(now)
        return sum(value for _, value in self.items)


class MarketWatch:
    def __init__(self, symbol: str, config: WatchConfig | None = None) -> None:
        self.symbol = symbol.upper()
        self.config = config or WatchConfig()
        span = self.config.window
        history = span + timedelta(minutes=1)
        self._spot_flow = _Window(span)
        self._futures_flow = _Window(span)
        self._prices = _Window(history)
        self._oi = _Window(history)
        self._long_liq = _Window(self.config.liquidation_window)
        self._short_liq = _Window(self.config.liquidation_window)
        self._hour_long_liq = _Window(timedelta(hours=1))
        self._hour_short_liq = _Window(timedelta(hours=1))
        self._hour_prices = _Window(timedelta(hours=1, minutes=1))
        self._last_price: float | None = None
        self._last_price_bucket: datetime | None = None
        self._last_derivatives: ExchangeDerivativesSnapshot | None = None
        self._last_alert: dict[str, datetime] = {}
        self._last_summary: datetime | None = None

    def on_trade(self, trade: TradeTick) -> list[WatchAlert]:
        price = float(trade.price)
        notional = price * float(trade.quantity)
        signed = notional if trade.side == TradeSide.BUY else -notional
        now = trade.event_time

        if trade.market_type == MarketType.SPOT:
            self._spot_flow.add(now, signed)
            self._record_price(now, price)
        else:
            self._futures_flow.add(now, signed)
            if self._last_price is None:
                self._record_price(now, price)

        if notional < self.config.whale_trade_usd:
            return []

        market = "SPOT" if trade.market_type == MarketType.SPOT else "VADELİ"
        action = "ALDI" if trade.side == TradeSide.BUY else "SATTI"
        alert = self._throttled(
            f"whale_trade_{market.lower()}_{trade.side.value}",
            now,
            (
                f"🐋 {self.symbol} büyük işlem\n"
                f"{market}: piyasa emriyle {_usd(notional)} {action}\n"
                f"Fiyat: {price:,.1f}"
            ),
        )
        return [alert] if alert else []

    def on_liquidation(self, event: LiquidationEvent) -> list[WatchAlert]:
        now = event.event_time
        notional = float(event.notional_usd)
        if event.position_side == LiquidatedPositionSide.LONG:
            self._long_liq.add(now, notional)
            self._hour_long_liq.add(now, notional)
            window, side = self._long_liq, "LONG"
            meaning = "fiyat düşerken long'lar zorla kapatılıyor"
        else:
            self._short_liq.add(now, notional)
            self._hour_short_liq.add(now, notional)
            window, side = self._short_liq, "SHORT"
            meaning = "fiyat yükselirken short'lar zorla kapatılıyor"

        total = window.total(now)
        if total < self.config.liquidation_wave_usd:
            return []

        alert = self._throttled(
            f"liquidation_{side.lower()}",
            now,
            (
                f"💥 {self.symbol} {side} likidasyon dalgası\n"
                f"Son {self._minutes(self.config.liquidation_window)} dk: "
                f"{_usd(total)} {side} likide oldu ({meaning}).\n"
                f"Fiyat: {float(event.price):,.1f}"
            ),
        )
        return [alert] if alert else []

    def on_derivatives(
        self,
        snapshot: ExchangeDerivativesSnapshot,
    ) -> list[WatchAlert]:
        now = snapshot.timestamp
        self._last_derivatives = snapshot
        if snapshot.open_interest is not None:
            self._oi.add(now, float(snapshot.open_interest))

        alerts: list[WatchAlert] = []
        positioning = self._positioning_alert(now)
        if positioning:
            alerts.append(positioning)
        funding = self._funding_alert(snapshot)
        if funding:
            alerts.append(funding)
        return alerts

    def tick(self, now: datetime) -> list[WatchAlert]:
        alerts: list[WatchAlert] = []
        flow = self._flow_alert(now)
        if flow:
            alerts.append(flow)

        summary_due = (
            self._last_summary is None
            or now - self._last_summary >= self.config.summary_every
        )
        if summary_due and self._last_price is not None:
            alerts.append(self.summary(now))
            self._last_summary = now
        return alerts

    def price_change_pct(self, now: datetime) -> float | None:
        return self._change_pct(self._prices, now, self.config.window)

    def oi_change_pct(self, now: datetime) -> float | None:
        return self._change_pct(self._oi, now, self.config.window)

    def spot_net_flow(self, now: datetime) -> float:
        return self._spot_flow.total(now)

    def futures_net_flow(self, now: datetime) -> float:
        return self._futures_flow.total(now)

    def _positioning_alert(self, now: datetime) -> WatchAlert | None:
        price = self.price_change_pct(now)
        oi = self.oi_change_pct(now)
        if price is None or oi is None:
            return None

        cfg = self.config
        if abs(price) < cfg.price_move_pct or abs(oi) < cfg.oi_move_pct:
            return None

        if price > 0 and oi > 0:
            kind = "positioning_new_longs"
            title = "📈 Yeni kaldıraçlı LONG'lar açılıyor"
            note = (
                "Fiyat ve açık pozisyon birlikte artıyor. Yükseliş yeni "
                "kaldıraçla destekleniyor; ani düşüşte long likidasyonu "
                "riski büyür."
            )
        elif price > 0 and oi < 0:
            kind = "positioning_short_cover"
            title = "📈 SHORT'lar kapanıyor"
            note = (
                "Fiyat yükselirken açık pozisyon azalıyor. Yükselişin bir "
                "kısmı short kapatmadan geliyor olabilir; kalıcılığı daha belirsiz."
            )
        elif price < 0 and oi > 0:
            kind = "positioning_new_shorts"
            title = "📉 Yeni kaldıraçlı SHORT'lar açılıyor"
            note = (
                "Fiyat düşerken açık pozisyon artıyor. Düşüş yeni short'larla "
                "destekleniyor; ani yükselişte short likidasyonu riski büyür."
            )
        else:
            kind = "positioning_long_exit"
            title = "📉 LONG'lar kapanıyor"
            note = (
                "Fiyat ve açık pozisyon birlikte düşüyor. Long'lar çıkıyor "
                "ya da likide oluyor; kaldıraç temizleniyor."
            )

        return self._throttled(
            kind,
            now,
            (
                f"{title} — {self.symbol}\n"
                f"Son {self._minutes(cfg.window)} dk: fiyat {_pct(price)}, "
                f"açık pozisyon (OI) {_pct(oi)}\n"
                f"{note}"
            ),
        )

    def _funding_alert(
        self,
        snapshot: ExchangeDerivativesSnapshot,
    ) -> WatchAlert | None:
        if snapshot.funding_rate is None:
            return None

        rate = float(snapshot.funding_rate)
        if rate >= self.config.funding_high:
            return self._throttled(
                "funding_high",
                snapshot.timestamp,
                (
                    f"🔥 {self.symbol} funding çok yüksek: {rate * 100:.4f}% "
                    "(normal: 0.0100%)\n"
                    "Long tarafı kalabalık ve kaldıraç için pahalı ödeme yapıyor."
                ),
            )
        if rate <= self.config.funding_low:
            return self._throttled(
                "funding_negative",
                snapshot.timestamp,
                (
                    f"🧊 {self.symbol} funding negatif: {rate * 100:.4f}%\n"
                    "Short tarafı kalabalık; short'lar long'lara ödeme yapıyor."
                ),
            )
        return None

    def _flow_alert(self, now: datetime) -> WatchAlert | None:
        price = self.price_change_pct(now)
        if price is None or abs(price) < self.config.price_move_pct:
            return None

        spot = self.spot_net_flow(now)
        futures = self.futures_net_flow(now)
        minimum = self.config.flow_min_usd
        window = self._minutes(self.config.window)

        if price > 0 and futures >= minimum and spot <= -minimum:
            return self._throttled(
                "flow_futures_led_up",
                now,
                (
                    f"⚠️ {self.symbol} yükselişi VADELİ alımlar taşıyor\n"
                    f"Son {window} dk: fiyat {_pct(price)}\n"
                    f"Vadeli net alım {_usd(futures)}, spot net {_usd(spot)} "
                    "(spot satıyor)\n"
                    "Kaldıraç kaynaklı yükseliş genelde daha kırılgandır."
                ),
            )
        if price > 0 and spot >= minimum and futures >= 0:
            return self._throttled(
                "flow_spot_led_up",
                now,
                (
                    f"✅ {self.symbol} yükselişi SPOT alımlar taşıyor\n"
                    f"Son {window} dk: fiyat {_pct(price)}\n"
                    f"Spot net alım {_usd(spot)}, vadeli net {_usd(futures)}\n"
                    "Gerçek alıcı kaynaklı yükseliş."
                ),
            )
        if price < 0 and futures <= -minimum and spot >= minimum:
            return self._throttled(
                "flow_futures_led_down",
                now,
                (
                    f"⚠️ {self.symbol} düşüşü VADELİ satışlar taşıyor\n"
                    f"Son {window} dk: fiyat {_pct(price)}\n"
                    f"Vadeli net satış {_usd(futures)}, spot net {_usd(spot)} "
                    "(spot alıyor)"
                ),
            )
        if price < 0 and spot <= -minimum and futures <= 0:
            return self._throttled(
                "flow_spot_led_down",
                now,
                (
                    f"🔻 {self.symbol} düşüşü SPOT satışlar taşıyor\n"
                    f"Son {window} dk: fiyat {_pct(price)}\n"
                    f"Spot net satış {_usd(spot)}, vadeli net {_usd(futures)}"
                ),
            )
        return None

    def summary(self, now: datetime) -> WatchAlert:
        price_1h = self._change_pct(
            self._hour_prices,
            now,
            timedelta(hours=1),
        )
        derivatives = self._last_derivatives
        lines = [
            f"🕐 {self.symbol} saatlik özet",
            f"Fiyat: {self._last_price:,.1f} (1s {_pct(price_1h)})",
            (
                f"Son {self._minutes(self.config.window)} dk net akış: "
                f"spot {_usd(self.spot_net_flow(now))}, "
                f"vadeli {_usd(self.futures_net_flow(now))}"
            ),
            (
                f"OI {self._minutes(self.config.window)} dk: "
                f"{_pct(self.oi_change_pct(now))}"
            ),
            (
                "Son 1s likidasyon: "
                f"long {_usd(self._hour_long_liq.total(now))}, "
                f"short {_usd(self._hour_short_liq.total(now))}"
            ),
        ]
        if derivatives is not None:
            if derivatives.funding_rate is not None:
                lines.append(
                    f"Funding: {float(derivatives.funding_rate) * 100:.4f}%"
                )
            if derivatives.long_short_ratio is not None:
                lines.append(
                    "Long/short (hesap): "
                    f"{float(derivatives.long_short_ratio):.2f}"
                )
            if derivatives.top_trader_long_short_ratio is not None:
                lines.append(
                    "Top trader long/short: "
                    f"{float(derivatives.top_trader_long_short_ratio):.2f}"
                )
        return WatchAlert(
            kind="summary",
            timestamp=now,
            text="\n".join(lines),
        )

    def _record_price(self, now: datetime, price: float) -> None:
        self._last_price = price
        bucket = now.replace(second=0, microsecond=0)
        if self._last_price_bucket != bucket:
            self._prices.add(now, price)
            self._hour_prices.add(now, price)
            self._last_price_bucket = bucket

    def _change_pct(
        self,
        window: _Window,
        now: datetime,
        span: timedelta,
    ) -> float | None:
        window.trim(now)
        if not window.items:
            return None

        start_time, start_value = window.items[0]
        if now - start_time < span - timedelta(minutes=1):
            return None

        latest = window.items[-1][1]
        if window is self._prices or window is self._hour_prices:
            latest = self._last_price if self._last_price is not None else latest
        if start_value == 0:
            return None
        return (latest / start_value - 1.0) * 100.0

    def _throttled(
        self,
        kind: str,
        now: datetime,
        text: str,
    ) -> WatchAlert | None:
        last = self._last_alert.get(kind)
        if last is not None and now - last < self.config.cooldown:
            return None
        self._last_alert[kind] = now
        return WatchAlert(kind=kind, timestamp=now, text=text)

    @staticmethod
    def _minutes(span: timedelta) -> int:
        return int(span.total_seconds() // 60)
