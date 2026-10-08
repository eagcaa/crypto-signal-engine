from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from crypto_signal_engine.features.research import ResearchFeatureSnapshot
from crypto_signal_engine.market import MarketSnapshot
from crypto_signal_engine.predictions.models import (
    Prediction,
    PredictionDecision,
    PredictionDecisionDirection,
    PredictionDirection,
)


class BaselinePredictionEngine:
    """Research-only order-book baseline kept for comparison."""

    def __init__(
        self,
        *,
        minimum_data_quality: Decimal = Decimal("0.80"),
        minimum_abs_score: Decimal = Decimal("0.30"),
    ) -> None:
        self._minimum_data_quality = minimum_data_quality
        self._minimum_abs_score = minimum_abs_score

    def generate(
        self,
        snapshot: MarketSnapshot,
        *,
        horizon_seconds: int,
    ) -> Prediction | None:
        if snapshot.price is None:
            return None
        if snapshot.data_quality < self._minimum_data_quality:
            return None
        if (
            snapshot.binance_book_imbalance is None
            or snapshot.bybit_book_imbalance is None
        ):
            return None

        raw_score = (
            snapshot.binance_book_imbalance
            + snapshot.bybit_book_imbalance
        ) / Decimal("2")

        if abs(raw_score) < self._minimum_abs_score:
            return None

        direction = (
            PredictionDirection.LONG
            if raw_score > 0
            else PredictionDirection.SHORT
        )

        return Prediction(
            id=uuid4(),
            symbol=snapshot.symbol,
            created_at=snapshot.timestamp,
            expires_at=snapshot.timestamp + timedelta(seconds=horizon_seconds),
            horizon_seconds=horizon_seconds,
            direction=direction,
            entry_price=snapshot.price,
            raw_score=raw_score,
            data_quality=snapshot.data_quality,
        )


class CompositePredictionEngine:
    """Transparent rule-based v2 research engine.

    The score is a research score in [-1, 1], not a calibrated probability.
    Missing feature groups are excluded and the remaining weights are
    renormalized. NO_TRADE is returned when the absolute score is too small.
    """

    WEIGHTS = {
        "order_book": Decimal("0.25"),
        "spot_cvd": Decimal("0.15"),
        "futures_cvd": Decimal("0.15"),
        "open_interest": Decimal("0.15"),
        "funding": Decimal("0.05"),
        "crowding": Decimal("0.10"),
        "taker_flow": Decimal("0.10"),
        "liquidations": Decimal("0.05"),
    }

    def __init__(
        self,
        *,
        minimum_data_quality: Decimal = Decimal("0.80"),
        minimum_abs_score: Decimal = Decimal("0.20"),
    ) -> None:
        self._minimum_data_quality = minimum_data_quality
        self._minimum_abs_score = minimum_abs_score

    def decide(
        self,
        features: ResearchFeatureSnapshot,
        *,
        horizon_seconds: int,
    ) -> PredictionDecision:
        if features.price is None:
            return self._no_trade(
                features,
                horizon_seconds,
                reason="Price is unavailable.",
            )

        if features.market_data_quality < self._minimum_data_quality:
            return self._no_trade(
                features,
                horizon_seconds,
                reason="Market data quality is below the minimum threshold.",
            )

        signals: dict[str, Decimal | None] = {
            "order_book": self._order_book_score(features),
            "spot_cvd": self._flow_score(
                features.spot_cvd_1m,
                features.spot_cvd_5m,
                available=features.spot_trade_sources > 0,
            ),
            "futures_cvd": self._flow_score(
                features.futures_cvd_1m,
                features.futures_cvd_5m,
                available=features.futures_trade_sources > 0,
            ),
            "open_interest": self._open_interest_score(features),
            "funding": self._funding_score(features),
            "crowding": self._crowding_score(features),
            "taker_flow": self._ratio_direction_score(
                features.binance_taker_buy_sell_ratio
            ),
            "liquidations": features.liquidation_imbalance_5m,
        }

        active_weight = sum(
            (
                self.WEIGHTS[name]
                for name, value in signals.items()
                if value is not None
            ),
            Decimal("0"),
        )

        if active_weight == 0:
            return self._no_trade(
                features,
                horizon_seconds,
                reason="No usable feature groups are available.",
            )

        contributions: dict[str, Decimal] = {}
        raw_score = Decimal("0")

        for name, value in signals.items():
            if value is None:
                continue
            normalized_weight = self.WEIGHTS[name] / active_weight
            contribution = value * normalized_weight
            contributions[name] = contribution
            raw_score += contribution

        raw_score = self._clamp(raw_score)

        reason = self._reason(signals, raw_score)

        if abs(raw_score) < self._minimum_abs_score:
            return PredictionDecision(
                symbol=features.symbol,
                timestamp=features.timestamp,
                horizon_seconds=horizon_seconds,
                direction=PredictionDecisionDirection.NO_TRADE,
                raw_score=raw_score,
                feature_contributions=contributions,
                reason=reason,
                prediction=None,
            )

        direction = (
            PredictionDirection.LONG
            if raw_score > 0
            else PredictionDirection.SHORT
        )
        decision_direction = (
            PredictionDecisionDirection.LONG
            if direction == PredictionDirection.LONG
            else PredictionDecisionDirection.SHORT
        )

        prediction = Prediction(
            id=uuid4(),
            symbol=features.symbol,
            created_at=features.timestamp,
            expires_at=features.timestamp + timedelta(seconds=horizon_seconds),
            horizon_seconds=horizon_seconds,
            direction=direction,
            entry_price=features.price,
            raw_score=raw_score,
            data_quality=features.market_data_quality,
            model_name="composite_rules_v2",
            feature_contributions=contributions,
            reason=reason,
        )

        return PredictionDecision(
            symbol=features.symbol,
            timestamp=features.timestamp,
            horizon_seconds=horizon_seconds,
            direction=decision_direction,
            raw_score=raw_score,
            feature_contributions=contributions,
            reason=reason,
            prediction=prediction,
        )

    def _order_book_score(
        self,
        features: ResearchFeatureSnapshot,
    ) -> Decimal | None:
        values = [
            value
            for value in (
                features.binance_book_imbalance,
                features.bybit_book_imbalance,
            )
            if value is not None
        ]
        return self._average(values)

    def _flow_score(
        self,
        one_minute: Decimal,
        five_minutes: Decimal,
        *,
        available: bool,
    ) -> Decimal | None:
        if not available:
            return None
        return (
            self._sign(one_minute) * Decimal("0.60")
            + self._sign(five_minutes) * Decimal("0.40")
        )

    def _open_interest_score(
        self,
        features: ResearchFeatureSnapshot,
    ) -> Decimal | None:
        values = [
            value
            for value in (
                features.binance_oi_change_5m_pct,
                features.bybit_oi_change_5m_pct,
            )
            if value is not None
        ]
        average_change = self._average(values)
        if average_change is None:
            return None

        futures_direction = self._sign(features.futures_cvd_5m)
        if futures_direction == 0:
            return Decimal("0")

        # OI growth confirms the active futures-flow direction.
        # OI contraction weakens/opposes that direction.
        normalized_change = self._clamp(average_change / Decimal("0.50"))
        return normalized_change * futures_direction

    def _funding_score(
        self,
        features: ResearchFeatureSnapshot,
    ) -> Decimal | None:
        values = [
            value
            for value in (
                features.binance_funding_rate,
                features.bybit_funding_rate,
            )
            if value is not None
        ]
        average_funding = self._average(values)
        if average_funding is None:
            return None

        # Funding is used contrarian: crowded positive funding is bearish,
        # crowded negative funding is bullish.
        return -self._clamp(average_funding / Decimal("0.0005"))

    def _crowding_score(
        self,
        features: ResearchFeatureSnapshot,
    ) -> Decimal | None:
        ratios = [
            value
            for value in (
                features.binance_long_short_ratio,
                features.bybit_long_short_ratio,
                features.binance_top_trader_long_short_ratio,
            )
            if value is not None and value > 0
        ]
        if not ratios:
            return None

        scores = [
            self._clamp((Decimal("1") - ratio) / (Decimal("1") + ratio))
            for ratio in ratios
        ]
        return self._average(scores)

    def _ratio_direction_score(
        self,
        ratio: Decimal | None,
    ) -> Decimal | None:
        if ratio is None or ratio <= 0:
            return None
        return self._clamp(
            (ratio - Decimal("1")) / (ratio + Decimal("1")) * Decimal("2")
        )

    def _reason(
        self,
        signals: dict[str, Decimal | None],
        raw_score: Decimal,
    ) -> str:
        available = [
            (name, value)
            for name, value in signals.items()
            if value is not None
        ]
        strongest = sorted(
            available,
            key=lambda item: abs(item[1]),
            reverse=True,
        )[:3]

        parts = [
            f"{name}={value:+.3f}"
            for name, value in strongest
        ]

        if raw_score >= self._minimum_abs_score:
            prefix = "Bullish composite"
        elif raw_score <= -self._minimum_abs_score:
            prefix = "Bearish composite"
        else:
            prefix = "Mixed/weak composite"

        return f"{prefix}; strongest signals: " + ", ".join(parts)

    def _no_trade(
        self,
        features: ResearchFeatureSnapshot,
        horizon_seconds: int,
        *,
        reason: str,
    ) -> PredictionDecision:
        return PredictionDecision(
            symbol=features.symbol,
            timestamp=features.timestamp,
            horizon_seconds=horizon_seconds,
            direction=PredictionDecisionDirection.NO_TRADE,
            raw_score=Decimal("0"),
            feature_contributions={},
            reason=reason,
            prediction=None,
        )

    @staticmethod
    def _sign(value: Decimal) -> Decimal:
        if value > 0:
            return Decimal("1")
        if value < 0:
            return Decimal("-1")
        return Decimal("0")

    @staticmethod
    def _average(values: list[Decimal]) -> Decimal | None:
        if not values:
            return None
        return sum(values, Decimal("0")) / Decimal(len(values))

    @staticmethod
    def _clamp(value: Decimal) -> Decimal:
        return max(Decimal("-1"), min(Decimal("1"), value))
