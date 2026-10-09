from datetime import UTC, datetime
from decimal import Decimal

from crypto_signal_engine.replay import (
    ReplayPricePoint,
    ReplayResult,
    select_research_replay_inputs,
)


def _result() -> ReplayResult:
    return ReplayResult(
        decisions=(),
        predictions=(),
        evaluations=(),
        open_predictions=(),
    )


def _point(price: str) -> ReplayPricePoint:
    return ReplayPricePoint(
        symbol="BTCUSDT",
        timestamp=datetime(2026, 10, 9, 8, 0, tzinfo=UTC),
        price=Decimal(price),
    )


def test_compare_mode_uses_exact_research_price_points_and_result() -> None:
    sampled_result = _result()
    exact_result = _result()
    sampled_points = [_point("100")]
    exact_points = [_point("101")]

    selected = select_research_replay_inputs(
        primary_result=sampled_result,
        primary_price_points=sampled_points,
        exact_result=exact_result,
        exact_price_points=exact_points,
        exact_binance_trades=False,
        compare_price_sources=True,
    )

    assert selected.result is exact_result
    assert selected.price_points is exact_points
    assert selected.price_source == "binance_spot_aggTrades"


def test_standard_replay_keeps_sampled_research_inputs() -> None:
    sampled_result = _result()
    sampled_points = [_point("100")]

    selected = select_research_replay_inputs(
        primary_result=sampled_result,
        primary_price_points=sampled_points,
        exact_result=None,
        exact_price_points=None,
        exact_binance_trades=False,
        compare_price_sources=False,
    )

    assert selected.result is sampled_result
    assert selected.price_points is sampled_points
    assert selected.price_source == "persisted_market_snapshots"
