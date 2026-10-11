"""Descriptive live market-state alerts.

These rules do not predict direction or create trade entries. They translate
already collected live market microstructure into compact decision-support
events for a human operator.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from crypto_signal_engine.features.research import ResearchFeatureSnapshot


@dataclass(frozen=True, slots=True)
class MarketStateAlert:
    key: str
    title: str
    summary: str
    details: tuple[str, ...]


class MarketStateAlertMonitor:
    """Emit human-readable market-state events with per-event cooldowns."""

    def __init__(
        self,
        *,
        cooldown: timedelta = timedelta(minutes=5),
        minimum_data_quality: Decimal = Decimal("0.67"),
        minimum_history_seconds: int = 60,
        oi_trigger_pct: Decimal = Decimal("0.25"),
        trend_trigger: Decimal = Decimal("0.15"),
        flow_trigger: Decimal = Decimal("0.10"),
        flow_divergence_trigger: Decimal = Decimal("0.25"),
        liquidation_notional_trigger_usd: Decimal = Decimal("250000"),
        liquidation_imbalance_trigger: Decimal = Decimal("0.65"),
        book_imbalance_trigger: Decimal = Decimal("0.30"),
    ) -> None:
        self._cooldown = cooldown
        self._minimum_data_quality = minimum_data_quality
        self._minimum_history_seconds = minimum_history_seconds
        self._oi_trigger_pct = oi_trigger_pct
        self._trend_trigger = trend_trigger
        self._flow_trigger = flow_trigger
        self._flow_divergence_trigger = flow_divergence_trigger
        self._liquidation_notional_trigger_usd = (
            liquidation_notional_trigger_usd
        )
        self._liquidation_imbalance_trigger = (
            liquidation_imbalance_trigger
        )
        self._book_imbalance_trigger = book_imbalance_trigger
        self._last_emitted_at: dict[str, datetime] = {}

    def evaluate(
        self,
        snapshot: ResearchFeatureSnapshot,
    ) -> tuple[MarketStateAlert, ...]:
        if (
            snapshot.market_data_quality < self._minimum_data_quality
            or snapshot.history_seconds < self._minimum_history_seconds
        ):
            return ()

        candidates: list[MarketStateAlert] = []
        candidates.extend(self._positioning_alerts(snapshot))
        candidates.extend(self._flow_alerts(snapshot))
        candidates.extend(self._liquidation_alerts(snapshot))
        candidates.extend(self._book_alerts(snapshot))

        output: list[MarketStateAlert] = []
        for alert in candidates:
            previous = self._last_emitted_at.get(alert.key)
            if (
                previous is not None
                and snapshot.timestamp - previous < self._cooldown
            ):
                continue
            self._last_emitted_at[alert.key] = snapshot.timestamp
            output.append(alert)

        return tuple(output)

    def _positioning_alerts(
        self,
        snapshot: ResearchFeatureSnapshot,
    ) -> list[MarketStateAlert]:
        oi_values = [
            value
            for value in (
                snapshot.binance_oi_change_5m_pct,
                snapshot.bybit_oi_change_5m_pct,
            )
            if value is not None
        ]
        if not oi_values or snapshot.trend_score_5m is None:
            return []

        average_oi = sum(oi_values, Decimal("0")) / Decimal(
            len(oi_values)
        )
        trend = snapshot.trend_score_5m
        futures_flow = snapshot.futures_cvd_ratio_5m
        funding = self._average_optional(
            snapshot.binance_funding_rate,
            snapshot.bybit_funding_rate,
        )
        details = (
            f"OI 5m avg: {average_oi:+.3f}%",
            f"Futures CVD ratio 5m: {futures_flow:+.3f}",
            f"Trend 5m: {trend:+.3f}",
            (
                "Funding avg: n/a"
                if funding is None
                else f"Funding avg: {funding:+.6f}"
            ),
        )

        if (
            average_oi >= self._oi_trigger_pct
            and trend >= self._trend_trigger
            and futures_flow >= self._flow_trigger
        ):
            return [
                MarketStateAlert(
                    key="leveraged_longs_building",
                    title="Kaldıraçlı long birikimi",
                    summary=(
                        "Fiyat yukarı eğilimli, açık pozisyon artıyor ve "
                        "futures alım akışı pozitif."
                    ),
                    details=details,
                )
            ]

        if (
            average_oi >= self._oi_trigger_pct
            and trend <= -self._trend_trigger
            and futures_flow <= -self._flow_trigger
        ):
            return [
                MarketStateAlert(
                    key="leveraged_shorts_building",
                    title="Kaldıraçlı short birikimi",
                    summary=(
                        "Fiyat aşağı eğilimli, açık pozisyon artıyor ve "
                        "futures satış akışı negatif."
                    ),
                    details=details,
                )
            ]

        if (
            average_oi <= -self._oi_trigger_pct
            and trend >= self._trend_trigger
        ):
            return [
                MarketStateAlert(
                    key="short_covering",
                    title="Short kapanışı ağırlıklı yükseliş",
                    summary=(
                        "Fiyat yükselirken açık pozisyon azalıyor; hareketin "
                        "bir kısmı short kapanışından geliyor olabilir."
                    ),
                    details=details,
                )
            ]

        if (
            average_oi <= -self._oi_trigger_pct
            and trend <= -self._trend_trigger
        ):
            return [
                MarketStateAlert(
                    key="long_unwind",
                    title="Long kapanışı ağırlıklı düşüş",
                    summary=(
                        "Fiyat düşerken açık pozisyon azalıyor; long "
                        "pozisyonların çözülmesi öne çıkıyor olabilir."
                    ),
                    details=details,
                )
            ]

        return []

    def _flow_alerts(
        self,
        snapshot: ResearchFeatureSnapshot,
    ) -> list[MarketStateAlert]:
        spot = snapshot.spot_cvd_ratio_5m
        futures = snapshot.futures_cvd_ratio_5m
        divergence = spot - futures

        if (
            divergence >= self._flow_divergence_trigger
            and spot >= self._flow_trigger
        ):
            return [
                MarketStateAlert(
                    key="spot_leads_buying",
                    title="Spot talebi futures'ın önünde",
                    summary=(
                        "Alım baskısı spotta futures'a göre belirgin daha "
                        "güçlü; hareket gerçek spot talebiyle destekleniyor "
                        "olabilir."
                    ),
                    details=(
                        f"Spot CVD ratio 5m: {spot:+.3f}",
                        f"Futures CVD ratio 5m: {futures:+.3f}",
                        f"Spot-futures farkı: {divergence:+.3f}",
                    ),
                )
            ]

        if (
            divergence <= -self._flow_divergence_trigger
            and futures >= self._flow_trigger
        ):
            return [
                MarketStateAlert(
                    key="futures_leads_buying",
                    title="Futures alımı spotun önünde",
                    summary=(
                        "Alım baskısı ağırlıklı olarak futures tarafında; "
                        "hareket kaldıraçlı talebe daha bağımlı olabilir."
                    ),
                    details=(
                        f"Spot CVD ratio 5m: {spot:+.3f}",
                        f"Futures CVD ratio 5m: {futures:+.3f}",
                        f"Spot-futures farkı: {divergence:+.3f}",
                    ),
                )
            ]

        if (
            divergence <= -self._flow_divergence_trigger
            and spot <= -self._flow_trigger
        ):
            return [
                MarketStateAlert(
                    key="spot_leads_selling",
                    title="Spot satışı futures'ın önünde",
                    summary=(
                        "Satış baskısı spotta futures'a göre daha güçlü; "
                        "gerçek spot arzı öne çıkıyor olabilir."
                    ),
                    details=(
                        f"Spot CVD ratio 5m: {spot:+.3f}",
                        f"Futures CVD ratio 5m: {futures:+.3f}",
                        f"Spot-futures farkı: {divergence:+.3f}",
                    ),
                )
            ]

        if (
            divergence >= self._flow_divergence_trigger
            and futures <= -self._flow_trigger
        ):
            return [
                MarketStateAlert(
                    key="futures_leads_selling",
                    title="Futures satışı spotun önünde",
                    summary=(
                        "Satış baskısı ağırlıklı olarak futures tarafında; "
                        "kaldıraçlı satış veya hedge baskısı öne çıkıyor "
                        "olabilir."
                    ),
                    details=(
                        f"Spot CVD ratio 5m: {spot:+.3f}",
                        f"Futures CVD ratio 5m: {futures:+.3f}",
                        f"Spot-futures farkı: {divergence:+.3f}",
                    ),
                )
            ]

        return []

    def _liquidation_alerts(
        self,
        snapshot: ResearchFeatureSnapshot,
    ) -> list[MarketStateAlert]:
        if not snapshot.liquidation_data_available:
            return []

        total = (
            snapshot.long_liquidations_5m_usd
            + snapshot.short_liquidations_5m_usd
        )
        imbalance = snapshot.liquidation_imbalance_5m
        if total < self._liquidation_notional_trigger_usd:
            return []

        if imbalance >= self._liquidation_imbalance_trigger:
            return [
                MarketStateAlert(
                    key="short_liquidation_wave",
                    title="Short liquidation dalgası",
                    summary=(
                        "Son 5 dakikadaki likidasyonların büyük bölümü "
                        "short pozisyonlarda."
                    ),
                    details=(
                        f"Toplam 5m USD: {total:,.0f}",
                        (
                            "Short liquidations USD: "
                            f"{snapshot.short_liquidations_5m_usd:,.0f}"
                        ),
                        (
                            "Long liquidations USD: "
                            f"{snapshot.long_liquidations_5m_usd:,.0f}"
                        ),
                        f"Imbalance: {imbalance:+.3f}",
                    ),
                )
            ]

        if imbalance <= -self._liquidation_imbalance_trigger:
            return [
                MarketStateAlert(
                    key="long_liquidation_wave",
                    title="Long liquidation dalgası",
                    summary=(
                        "Son 5 dakikadaki likidasyonların büyük bölümü "
                        "long pozisyonlarda."
                    ),
                    details=(
                        f"Toplam 5m USD: {total:,.0f}",
                        (
                            "Long liquidations USD: "
                            f"{snapshot.long_liquidations_5m_usd:,.0f}"
                        ),
                        (
                            "Short liquidations USD: "
                            f"{snapshot.short_liquidations_5m_usd:,.0f}"
                        ),
                        f"Imbalance: {imbalance:+.3f}",
                    ),
                )
            ]

        return []

    def _book_alerts(
        self,
        snapshot: ResearchFeatureSnapshot,
    ) -> list[MarketStateAlert]:
        binance = snapshot.binance_book_imbalance
        bybit = snapshot.bybit_book_imbalance
        if binance is None or bybit is None:
            return []

        if (
            binance >= self._book_imbalance_trigger
            and bybit >= self._book_imbalance_trigger
        ):
            return [
                MarketStateAlert(
                    key="cross_exchange_bid_pressure",
                    title="İki borsada alış defteri baskısı",
                    summary=(
                        "Binance ve Bybit order book aynı anda bid-ağır. "
                        "Bu henüz duvarın kalıcı olduğunu kanıtlamaz."
                    ),
                    details=(
                        f"Binance imbalance: {binance:+.3f}",
                        f"Bybit imbalance: {bybit:+.3f}",
                    ),
                )
            ]

        if (
            binance <= -self._book_imbalance_trigger
            and bybit <= -self._book_imbalance_trigger
        ):
            return [
                MarketStateAlert(
                    key="cross_exchange_ask_pressure",
                    title="İki borsada satış defteri baskısı",
                    summary=(
                        "Binance ve Bybit order book aynı anda ask-ağır. "
                        "Bu henüz duvarın kalıcı olduğunu kanıtlamaz."
                    ),
                    details=(
                        f"Binance imbalance: {binance:+.3f}",
                        f"Bybit imbalance: {bybit:+.3f}",
                    ),
                )
            ]

        return []

    @staticmethod
    def _average_optional(
        *values: Decimal | None,
    ) -> Decimal | None:
        available = [value for value in values if value is not None]
        if not available:
            return None
        return sum(available, Decimal("0")) / Decimal(len(available))
