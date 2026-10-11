from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from crypto_signal_engine.examples.h5_funding_basis_baseline import (
    CostModel,
    PricePoint,
    _parse_archive_timestamp,
)
from crypto_signal_engine.examples.h5b_two_sided_carry import (
    H5bPosition,
    coverage_ratio,
    down_resize,
    same_hour_tradable,
    simulate_h5b,
    up_resize,
)


COSTS = CostModel(
    name="test",
    spot_execution_rate=Decimal("0.0012"),
    futures_execution_rate=Decimal("0.0007"),
)


def _point(hour: int, price: str) -> PricePoint:
    return PricePoint(
        timestamp=datetime(2024, 1, 1, tzinfo=UTC)
        + timedelta(hours=hour, minutes=59, seconds=59, milliseconds=999),
        close=Decimal(price),
    )


def test_h5b_down_resize_restores_coverage_to_one_after_costs() -> None:
    position = H5bPosition(
        quantity=Decimal("1"),
        quantity_cap=Decimal("1"),
        short_entry_price=Decimal("100"),
        margin_cash=Decimal("100"),
    )

    down_resize(
        position,
        spot_price=Decimal("160"),
        perp_price=Decimal("160"),
        leverage=Decimal("1"),
        cost_model=COSTS,
    )

    assert coverage_ratio(
        position,
        perp_price=Decimal("160"),
        leverage=Decimal("1"),
    ) == pytest.approx(Decimal("1"))


def test_h5b_up_resize_restores_coverage_to_one_after_costs() -> None:
    position = H5bPosition(
        quantity=Decimal("0.5"),
        quantity_cap=Decimal("2"),
        short_entry_price=Decimal("100"),
        margin_cash=Decimal("100"),
    )

    result = up_resize(
        position,
        spot_price=Decimal("50"),
        perp_price=Decimal("50"),
        leverage=Decimal("1"),
        cost_model=COSTS,
    )

    assert result is not None
    assert coverage_ratio(
        position,
        perp_price=Decimal("50"),
        leverage=Decimal("1"),
    ) == pytest.approx(Decimal("1"))


def test_h5b_up_resize_cap_binds() -> None:
    position = H5bPosition(
        quantity=Decimal("0.5"),
        quantity_cap=Decimal("0.75"),
        short_entry_price=Decimal("100"),
        margin_cash=Decimal("100"),
    )

    result = up_resize(
        position,
        spot_price=Decimal("50"),
        perp_price=Decimal("50"),
        leverage=Decimal("1"),
        cost_model=COSTS,
    )

    assert result is not None
    assert position.quantity == Decimal("0.75")
    assert coverage_ratio(
        position,
        perp_price=Decimal("50"),
        leverage=Decimal("1"),
    ) > Decimal("1")


def test_h5b_no_up_resize_at_quantity_cap() -> None:
    position = H5bPosition(
        quantity=Decimal("1"),
        quantity_cap=Decimal("1"),
        short_entry_price=Decimal("100"),
        margin_cash=Decimal("300"),
    )

    result = up_resize(
        position,
        spot_price=Decimal("50"),
        perp_price=Decimal("50"),
        leverage=Decimal("1"),
        cost_model=COSTS,
    )

    assert result is None
    assert position.quantity == Decimal("1")


def test_h5b_up_resize_updates_average_short_entry_price() -> None:
    position = H5bPosition(
        quantity=Decimal("0.5"),
        quantity_cap=Decimal("2"),
        short_entry_price=Decimal("100"),
        margin_cash=Decimal("100"),
    )

    result = up_resize(
        position,
        spot_price=Decimal("50"),
        perp_price=Decimal("50"),
        leverage=Decimal("1"),
        cost_model=CostModel(
            name="zero",
            spot_execution_rate=Decimal("0"),
            futures_execution_rate=Decimal("0"),
        ),
    )

    assert result is not None
    expected = (
        Decimal("0.5") * Decimal("100")
        + result.quantity * Decimal("50")
    ) / (Decimal("0.5") + result.quantity)
    assert position.short_entry_price == expected


def test_h5b_missing_spot_hour_does_not_execute_resize() -> None:
    spot = [_point(0, "100")]
    spot.extend(_point(hour, "100") for hour in range(2, 26))

    perp = [_point(0, "100"), _point(1, "200")]
    perp.extend(_point(hour, "100") for hour in range(2, 26))

    result = simulate_h5b(
        leverage=Decimal("1"),
        spot_prices=spot,
        perp_prices=perp,
        funding=[],
        cost_model=CostModel(
            name="zero",
            spot_execution_rate=Decimal("0"),
            futures_execution_rate=Decimal("0"),
        ),
    )

    assert not same_hour_tradable(spot[0], perp[1])
    assert result.down_resizes == 0
    assert result.up_resizes == 0


def test_h5b_final_year_return_includes_exit_costs() -> None:
    spot = [_point(hour, "100") for hour in range(26)]
    perp = [_point(hour, "100") for hour in range(26)]

    result = simulate_h5b(
        leverage=Decimal("1"),
        spot_prices=spot,
        perp_prices=perp,
        funding=[],
        cost_model=COSTS,
    )

    only_year = next(iter(result.yearly_returns_pct))
    assert result.yearly_returns_pct[only_year] == result.net_return_pct
    assert result.net_return_pct < 0


def test_h5b_timestamp_parser_accepts_milliseconds_and_microseconds() -> None:
    millis = _parse_archive_timestamp("1704067200000")
    micros = _parse_archive_timestamp("1735689600000000")

    assert millis == datetime(2024, 1, 1, tzinfo=UTC)
    assert micros == datetime(2025, 1, 1, tzinfo=UTC)



def test_h5b_pending_down_resize_executes_when_spot_returns() -> None:
    spot = [_point(0, "100")]
    spot.extend(_point(hour, "200") for hour in range(2, 26))

    perp = [_point(0, "100"), _point(1, "200")]
    perp.extend(_point(hour, "200") for hour in range(2, 26))

    result = simulate_h5b(
        leverage=Decimal("1"),
        spot_prices=spot,
        perp_prices=perp,
        funding=[],
        cost_model=CostModel(
            name="zero",
            spot_execution_rate=Decimal("0"),
            futures_execution_rate=Decimal("0"),
        ),
    )

    assert result.down_resizes == 1


def test_h5b_year_boundary_only_final_year_includes_exit_costs() -> None:
    start = datetime(2024, 12, 31, 22, 59, 59, 999000, tzinfo=UTC)
    spot = [
        PricePoint(timestamp=start, close=Decimal("100")),
        PricePoint(
            timestamp=start + timedelta(hours=1),
            close=Decimal("100"),
        ),
        PricePoint(
            timestamp=start + timedelta(hours=2),
            close=Decimal("100"),
        ),
    ]
    perp = list(spot)

    result = simulate_h5b(
        leverage=Decimal("1"),
        spot_prices=spot,
        perp_prices=perp,
        funding=[],
        cost_model=COSTS,
    )

    entry_only_return = (
        (
            result.starting_capital
            - Decimal("10000") * COSTS.spot_execution_rate
            - Decimal("10000") * COSTS.futures_execution_rate
        )
        / result.starting_capital
        - Decimal("1")
    ) * Decimal("100")

    assert result.yearly_returns_pct[2024] == entry_only_return
    assert result.yearly_returns_pct[2025] < Decimal("0")
    assert result.yearly_returns_pct[2025] < result.yearly_returns_pct[2024]
