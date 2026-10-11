from datetime import UTC, datetime
from decimal import Decimal

import pytest

from crypto_signal_engine.examples.h5b_final_test import (
    EXPECTED_HOURLY_POINTS,
    _assert_final_timestamp,
    evaluate_primary_gate,
)
from crypto_signal_engine.examples.h5b_two_sided_carry import (
    H5bSimulationResult,
)


def _result(
    *,
    net_return: str = "10",
    annualized: str = "8",
    stress: bool = True,
    return_2024: str = "8",
    return_2025: str = "8",
) -> H5bSimulationResult:
    return H5bSimulationResult(
        leverage=Decimal("1"),
        cost_model="detailed_0.38pct",
        starting_capital=Decimal("20000"),
        ending_equity=Decimal("22000"),
        net_return_pct=Decimal(net_return),
        annualized_return_pct=Decimal(annualized),
        funding_income=Decimal("1000"),
        trading_costs=Decimal("100"),
        down_resizes=1,
        up_resizes=1,
        resized_btc=Decimal("0.5"),
        max_drawdown_pct=Decimal("2"),
        minimum_margin_equity=Decimal("5000"),
        worst_24h_perp_rise_pct=Decimal("20"),
        static_margin_stress_survives=stress,
        yearly_returns_pct={
            2024: Decimal(return_2024),
            2025: Decimal(return_2025),
        },
    )


def test_h5b_final_expected_hour_count_is_frozen() -> None:
    assert EXPECTED_HOURLY_POINTS == 17_544


def test_h5b_final_timestamp_guard_rejects_outside_period() -> None:
    with pytest.raises(RuntimeError, match="non-final-test"):
        _assert_final_timestamp(
            datetime(2023, 12, 31, 23, 59, tzinfo=UTC),
            source="test",
        )

    with pytest.raises(RuntimeError, match="non-final-test"):
        _assert_final_timestamp(
            datetime(2026, 1, 1, tzinfo=UTC),
            source="test",
        )


def test_h5b_primary_gate_passes_only_frozen_conditions() -> None:
    result = _result(
        annualized="8",
        return_2024="7",
        return_2025="6",
    )

    passes, excess, yearly = evaluate_primary_gate(
        result,
        benchmark_annualized_pct=Decimal("4"),
        benchmark_yearly_pct={
            2024: Decimal("3"),
            2025: Decimal("3"),
        },
    )

    assert passes
    assert excess == Decimal("4")
    assert yearly == {
        2024: Decimal("4"),
        2025: Decimal("3"),
    }


@pytest.mark.parametrize(
    ("result", "benchmark_annualized", "benchmark_yearly"),
    [
        (
            _result(net_return="0"),
            Decimal("4"),
            {2024: Decimal("3"), 2025: Decimal("3")},
        ),
        (
            _result(return_2025="2"),
            Decimal("4"),
            {2024: Decimal("3"), 2025: Decimal("3")},
        ),
        (
            _result(annualized="6"),
            Decimal("4"),
            {2024: Decimal("3"), 2025: Decimal("3")},
        ),
        (
            _result(stress=False),
            Decimal("4"),
            {2024: Decimal("3"), 2025: Decimal("3")},
        ),
    ],
)
def test_h5b_primary_gate_rejects_any_failed_condition(
    result: H5bSimulationResult,
    benchmark_annualized: Decimal,
    benchmark_yearly: dict[int, Decimal],
) -> None:
    passes, _, _ = evaluate_primary_gate(
        result,
        benchmark_annualized_pct=benchmark_annualized,
        benchmark_yearly_pct=benchmark_yearly,
    )

    assert not passes
