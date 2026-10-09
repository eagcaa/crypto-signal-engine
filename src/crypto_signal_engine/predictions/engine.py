from dataclasses import dataclass
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

        barriers = self._barrier_percentages(
            features,
            horizon_seconds=horizon_seconds,
        )
        if barriers is None:
            return self._no_trade(
                features,
                horizon_seconds,
                reason="ATR is unavailable for dynamic risk barriers.",
            )

        take_profit_pct, stop_loss_pct = barriers
        minimum_economic_move = (
            self._round_trip_cost_pct + self._minimum_net_edge_pct
        )
        if take_profit_pct <= minimum_economic_move:
            return PredictionDecision(
                symbol=features.symbol,
                timestamp=features.timestamp,
                horizon_seconds=horizon_seconds,
                direction=PredictionDecisionDirection.NO_TRADE,
                raw_score=raw_score,
                feature_contributions=contributions,
                reason=(
                    f"{reason}; no trade: target {take_profit_pct:.4f}% "
                    f"does not clear cost+edge floor "
                    f"{minimum_economic_move:.4f}%."
                ),
                prediction=None,
            )

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


@dataclass(frozen=True, slots=True)
class _HorizonProfile:
    name: str
    weights: dict[str, Decimal]
    short_flow_window: str
    long_flow_window: str
    oi_window: str
    liquidation_window: str
    required_history_seconds: int


class CompositePredictionEngine:
    """Transparent horizon-specific rule-based research engine.

    The raw score is a research score in [-1, 1], not a calibrated
    probability/confidence value.
    """

    PROFILES = {
        300: _HorizonProfile(
            name="5m",
            weights={
                "order_book": Decimal("0.20"),
                "spot_cvd": Decimal("0.15"),
                "futures_cvd": Decimal("0.15"),
                "open_interest": Decimal("0.10"),
                "funding": Decimal("0.05"),
                "crowding": Decimal("0.10"),
                "taker_flow": Decimal("0.10"),
                "liquidations": Decimal("0.05"),
                "trend": Decimal("0.10"),
            },
            short_flow_window="1m",
            long_flow_window="5m",
            oi_window="5m",
            liquidation_window="5m",
            required_history_seconds=300,
        ),
        900: _HorizonProfile(
            name="15m",
            weights={
                "order_book": Decimal("0.10"),
                "spot_cvd": Decimal("0.18"),
                "futures_cvd": Decimal("0.18"),
                "open_interest": Decimal("0.15"),
                "funding": Decimal("0.05"),
                "crowding": Decimal("0.09"),
                "taker_flow": Decimal("0.05"),
                "liquidations": Decimal("0.05"),
                "trend": Decimal("0.15"),
            },
            short_flow_window="5m",
            long_flow_window="15m",
            oi_window="15m",
            liquidation_window="15m",
            required_history_seconds=900,
        ),
    }

    @classmethod
    def model_name_for_horizon(
        cls,
        horizon_seconds: int,
    ) -> str | None:
        profile = cls.PROFILES.get(horizon_seconds)
        if profile is None:
            return None
        return f"composite_rules_v4_{profile.name}"

    @classmethod
    def current_model_names(cls) -> tuple[tuple[int, str], ...]:
        return tuple(
            (
                horizon,
                f"composite_rules_v4_{profile.name}",
            )
            for horizon, profile in sorted(cls.PROFILES.items())
        )

    def __init__(
        self,
        *,
        minimum_data_quality: Decimal = Decimal("0.80"),
        minimum_abs_score: Decimal = Decimal("0.20"),
        fee_pct_per_side: Decimal = Decimal("0.05"),
        slippage_pct_per_side: Decimal = Decimal("0.01"),
        minimum_net_edge_pct: Decimal = Decimal("0.08"),
    ) -> None:
        self._minimum_data_quality = minimum_data_quality
        self._minimum_abs_score = minimum_abs_score
        self._round_trip_cost_pct = Decimal("2") * (
            fee_pct_per_side + slippage_pct_per_side
        )
        self._minimum_net_edge_pct = minimum_net_edge_pct

    def decide(
        self,
        features: ResearchFeatureSnapshot,
        *,
        horizon_seconds: int,
    ) -> PredictionDecision:
        profile = self.PROFILES.get(horizon_seconds)

        if profile is None:
            return self._no_trade(
                features,
                horizon_seconds,
                reason=f"Unsupported horizon: {horizon_seconds}s.",
            )

        if features.history_seconds < profile.required_history_seconds:
            return self._no_trade(
                features,
                horizon_seconds,
                reason=(
                    f"Warmup: {features.history_seconds}s/"
                    f"{profile.required_history_seconds}s history ready."
                ),
            )

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

        spot_short, spot_long = self._flow_values(
            features,
            market="spot",
            profile=profile,
        )
        futures_short, futures_long = self._flow_values(
            features,
            market="futures",
            profile=profile,
        )

        signals: dict[str, Decimal | None] = {
            "order_book": self._order_book_score(features),
            "spot_cvd": self._flow_score(
                spot_short,
                spot_long,
                source_count=features.spot_trade_sources,
            ),
            "futures_cvd": self._flow_score(
                futures_short,
                futures_long,
                source_count=features.futures_trade_sources,
            ),
            "open_interest": self._open_interest_score(features, profile),
            "funding": self._funding_score(features),
            "crowding": self._crowding_score(features),
            "taker_flow": self._ratio_direction_score(
                features.binance_taker_buy_sell_ratio
            ),
            "liquidations": self._liquidation_score(features, profile),
            "trend": self._trend_score(features, profile),
        }

        active_weight = sum(
            (
                profile.weights[name]
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

            contribution = value * profile.weights[name]
            contributions[name] = contribution
            raw_score += contribution

        raw_score = self._clamp(raw_score)
        reason = self._reason(profile, contributions, raw_score)

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
            take_profit_pct=take_profit_pct,
            stop_loss_pct=stop_loss_pct,
            model_name=self.model_name_for_horizon(horizon_seconds)
            or "composite_rules_v4_unknown",
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

    def _barrier_percentages(
        self,
        features: ResearchFeatureSnapshot,
        *,
        horizon_seconds: int,
    ) -> tuple[Decimal, Decimal] | None:
        if horizon_seconds == 300:
            atr_pct = features.atr_pct_5m
            if atr_pct is None:
                return None
            take_profit_pct = self._clamp_range(
                atr_pct * Decimal("1.25"),
                Decimal("0.20"),
                Decimal("0.45"),
            )
            stop_loss_pct = self._clamp_range(
                atr_pct * Decimal("0.75"),
                Decimal("0.12"),
                Decimal("0.28"),
            )
            return take_profit_pct, stop_loss_pct

        if horizon_seconds == 900:
            atr_pct = features.atr_pct_15m
            if atr_pct is None:
                return None
            take_profit_pct = self._clamp_range(
                atr_pct * Decimal("1.10"),
                Decimal("0.24"),
                Decimal("0.55"),
            )
            stop_loss_pct = self._clamp_range(
                atr_pct * Decimal("0.70"),
                Decimal("0.15"),
                Decimal("0.35"),
            )
            return take_profit_pct, stop_loss_pct

        return None

    @staticmethod
    def _clamp_range(
        value: Decimal,
        minimum: Decimal,
        maximum: Decimal,
    ) -> Decimal:
        return max(minimum, min(maximum, value))

    def _flow_values(
        self,
        features: ResearchFeatureSnapshot,
        *,
        market: str,
        profile: _HorizonProfile,
    ) -> tuple[Decimal, Decimal]:
        prefix = (
            "spot_cvd_ratio"
            if market == "spot"
            else "futures_cvd_ratio"
        )
        short_value = getattr(
            features,
            f"{prefix}_{profile.short_flow_window}",
        )
        long_value = getattr(
            features,
            f"{prefix}_{profile.long_flow_window}",
        )
        return short_value, long_value

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
        short_window: Decimal,
        long_window: Decimal,
        *,
        source_count: int,
    ) -> Decimal | None:
        if source_count <= 0:
            return None

        direction_score = (
            self._clamp(short_window) * Decimal("0.60")
            + self._clamp(long_window) * Decimal("0.40")
        )

        coverage = min(
            Decimal(source_count) / Decimal("2"),
            Decimal("1"),
        )
        return direction_score * coverage

    def _open_interest_score(
        self,
        features: ResearchFeatureSnapshot,
        profile: _HorizonProfile,
    ) -> Decimal | None:
        if profile.oi_window == "5m":
            values = (
                features.binance_oi_change_5m_pct,
                features.bybit_oi_change_5m_pct,
            )
            futures_flow = features.futures_cvd_ratio_5m
        else:
            values = (
                features.binance_oi_change_15m_pct,
                features.bybit_oi_change_15m_pct,
            )
            futures_flow = features.futures_cvd_ratio_15m

        available_values = [value for value in values if value is not None]
        average_change = self._average(available_values)

        if average_change is None:
            return None

        # OI expansion can confirm the direction of futures flow.
        # OI contraction is treated as deleveraging/position closing and
        # therefore does not reverse-confirm the opposite direction.
        if average_change <= 0:
            return Decimal("0")

        futures_direction = self._sign(futures_flow)
        if futures_direction == 0:
            return Decimal("0")

        normalized_change = self._clamp(
            average_change / Decimal("0.50")
        )
        return normalized_change * futures_direction

    def _liquidation_score(
        self,
        features: ResearchFeatureSnapshot,
        profile: _HorizonProfile,
    ) -> Decimal:
        if profile.liquidation_window == "15m":
            return features.liquidation_imbalance_15m
        return features.liquidation_imbalance_5m

    def _trend_score(
        self,
        features: ResearchFeatureSnapshot,
        profile: _HorizonProfile,
    ) -> Decimal | None:
        value = (
            features.trend_score_15m
            if profile.name == "15m"
            else features.trend_score_5m
        )
        return self._clamp(value) if value is not None else None

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

        return -self._clamp(
            average_funding / Decimal("0.0005")
        )

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
            self._clamp(
                (Decimal("1") - ratio)
                / (Decimal("1") + ratio)
            )
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
            (ratio - Decimal("1"))
            / (ratio + Decimal("1"))
            * Decimal("2")
        )

    def _reason(
        self,
        profile: _HorizonProfile,
        contributions: dict[str, Decimal],
        raw_score: Decimal,
    ) -> str:
        strongest = sorted(
            contributions.items(),
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

        return (
            f"{prefix} [{profile.name} profile]; strongest signals: "
            + ", ".join(parts)
        )

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
    def _average(
        values: list[Decimal],
    ) -> Decimal | None:
        if not values:
            return None
        return sum(values, Decimal("0")) / Decimal(len(values))

    @staticmethod
    def _clamp(value: Decimal) -> Decimal:
        return max(
            Decimal("-1"),
            min(Decimal("1"), value),
        )
