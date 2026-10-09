from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from crypto_signal_engine.predictions import Prediction, PredictionDirection
from crypto_signal_engine.replay import (
    CandidateGate,
    CandidateWindowResult,
    ReplayPricePoint,
    ReplayResult,
    build_barrier_sweep,
    build_candidate_leaderboard,
)


def _window(
    *,
    index: int,
    gate: CandidateGate,
    outcome: str,
) -> CandidateWindowResult:
    created_at = datetime(2026, 10, 9, index, 0, tzinfo=UTC)
    prediction = Prediction(
        id=uuid4(),
        symbol="BTCUSDT",
        created_at=created_at,
        expires_at=created_at + timedelta(minutes=15),
        horizon_seconds=900,
        direction=PredictionDirection.LONG,
        entry_price=Decimal("100"),
        raw_score=Decimal("0.30"),
        data_quality=Decimal("0.90"),
        model_name="test",
    )
    if outcome == "tp":
        price = Decimal("100.50")
    elif outcome == "sl":
        price = Decimal("99.70")
    else:
        price = Decimal("100.20")

    points = (
        ReplayPricePoint(
            symbol="BTCUSDT",
            timestamp=created_at + timedelta(minutes=1),
            price=price,
        ),
    )
    result = ReplayResult(
        decisions=(),
        predictions=(prediction,),
        evaluations=(),
        open_predictions=(),
    )
    row = build_barrier_sweep(
        result,
        list(points),
        round_trip_cost_pct=Decimal("0.12"),
        take_profit_grid=(Decimal("0.40"),),
        stop_loss_grid=(Decimal("0.18"),),
    )[0]
    return CandidateWindowResult(
        window_index=index,
        gate=gate,
        result=result,
        price_points=points,
        row=row,
    )


def test_candidate_leaderboard_promotes_consistent_frozen_candidate() -> None:
    gate = CandidateGate(
        name="15m_long_range_high",
        horizon_seconds=900,
        direction=PredictionDirection.LONG,
        required_trend_regime="range",
        required_volatility_regime="high",
        frozen_take_profit_pct=Decimal("0.40"),
        frozen_stop_loss_pct=Decimal("0.18"),
    )
    windows = [
        _window(index=1, gate=gate, outcome="tp"),
        _window(index=2, gate=gate, outcome="tp"),
        _window(index=3, gate=gate, outcome="sl"),
    ]

    row = build_candidate_leaderboard(
        windows,
        minimum_trades=3,
        minimum_active_windows=3,
        minimum_positive_window_ratio=Decimal("0.60"),
        minimum_profit_factor=Decimal("1.10"),
    )[0]

    assert row.windows_tested == 3
    assert row.active_windows == 3
    assert row.positive_windows == 2
    assert row.trades == 3
    assert row.expectancy_pct is not None
    assert row.expectancy_pct > 0
    assert row.profit_factor is not None
    assert row.profit_factor > Decimal("1.10")
    assert row.promotion_ready is True
    assert row.reasons == ()


def test_candidate_leaderboard_holds_under_sampled_candidate() -> None:
    gate = CandidateGate(
        name="candidate",
        horizon_seconds=900,
        direction=PredictionDirection.LONG,
        frozen_take_profit_pct=Decimal("0.40"),
        frozen_stop_loss_pct=Decimal("0.18"),
    )
    windows = [_window(index=1, gate=gate, outcome="tp")]

    row = build_candidate_leaderboard(windows)[0]

    assert row.promotion_ready is False
    assert "trades:1/30" in row.reasons
    assert "active_windows:1/3" in row.reasons


def test_candidate_leaderboard_ignores_unfrozen_candidates() -> None:
    gate = CandidateGate(
        name="research_only",
        horizon_seconds=300,
        direction=PredictionDirection.LONG,
    )
    windows = [_window(index=1, gate=gate, outcome="tp")]

    assert build_candidate_leaderboard(windows) == ()
