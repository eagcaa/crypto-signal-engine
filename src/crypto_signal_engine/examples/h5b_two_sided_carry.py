"""Frozen H5b two-sided rebalance mechanics.

This module contains the pre-registered H5b mechanics only. It does not
download or inspect the 2024-2025 final-test period.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from crypto_signal_engine.examples.h5_funding_basis_baseline import (
    INITIAL_SPOT_NOTIONAL,
    CostModel,
    FundingPoint,
    PricePoint,
    _annualized_return,
    _latest_price_at_or_before,
    worst_24h_perp_rise_pct,
)


DOWN_TRIGGER = Decimal("0.50")
UP_TRIGGER = Decimal("1.50")


@dataclass(slots=True)
class H5bPosition:
    quantity: Decimal
    quantity_cap: Decimal
    short_entry_price: Decimal
    margin_cash: Decimal


@dataclass(frozen=True, slots=True)
class ResizeResult:
    side: str
    quantity: Decimal
    execution_cost: Decimal
    spot_cash_transfer: Decimal


@dataclass(frozen=True, slots=True)
class H5bSimulationResult:
    leverage: Decimal
    cost_model: str
    starting_capital: Decimal
    ending_equity: Decimal
    net_return_pct: Decimal
    annualized_return_pct: Decimal
    funding_income: Decimal
    trading_costs: Decimal
    down_resizes: int
    up_resizes: int
    resized_btc: Decimal
    max_drawdown_pct: Decimal
    minimum_margin_equity: Decimal
    worst_24h_perp_rise_pct: Decimal
    static_margin_stress_survives: bool
    yearly_returns_pct: dict[int, Decimal]


def target_margin(
    *,
    quantity: Decimal,
    perp_price: Decimal,
    leverage: Decimal,
) -> Decimal:
    return quantity * perp_price / leverage


def margin_equity(
    position: H5bPosition,
    *,
    perp_price: Decimal,
) -> Decimal:
    return position.margin_cash + position.quantity * (
        position.short_entry_price - perp_price
    )


def coverage_ratio(
    position: H5bPosition,
    *,
    perp_price: Decimal,
    leverage: Decimal,
) -> Decimal:
    target = target_margin(
        quantity=position.quantity,
        perp_price=perp_price,
        leverage=leverage,
    )
    if target <= 0:
        raise ValueError("H5b target margin must be positive")
    return margin_equity(
        position,
        perp_price=perp_price,
    ) / target


def same_hour_tradable(
    spot_point: PricePoint,
    perp_point: PricePoint,
) -> bool:
    """True when the latest observed spot/perp candles belong to one hour."""
    spot_hour = spot_point.timestamp.replace(
        minute=0,
        second=0,
        microsecond=0,
    )
    perp_hour = perp_point.timestamp.replace(
        minute=0,
        second=0,
        microsecond=0,
    )
    return spot_hour == perp_hour


def down_resize(
    position: H5bPosition,
    *,
    spot_price: Decimal,
    perp_price: Decimal,
    leverage: Decimal,
    cost_model: CostModel,
) -> ResizeResult:
    equity = margin_equity(position, perp_price=perp_price)
    target = target_margin(
        quantity=position.quantity,
        perp_price=perp_price,
        leverage=leverage,
    )
    denominator = (
        spot_price
        * (Decimal("1") - cost_model.spot_execution_rate)
        - perp_price * cost_model.futures_execution_rate
        + perp_price / leverage
    )
    if denominator <= 0:
        raise RuntimeError("Invalid H5b down-resize denominator")

    quantity = (target - equity) / denominator
    if quantity <= 0 or quantity >= position.quantity:
        raise RuntimeError("Invalid H5b down-resize quantity")

    spot_gross = quantity * spot_price
    spot_cost = spot_gross * cost_model.spot_execution_rate
    futures_notional = quantity * perp_price
    futures_cost = (
        futures_notional * cost_model.futures_execution_rate
    )
    realized_short_pnl = quantity * (
        position.short_entry_price - perp_price
    )

    position.margin_cash += (
        realized_short_pnl
        + spot_gross
        - spot_cost
        - futures_cost
    )
    position.quantity -= quantity

    return ResizeResult(
        side="down",
        quantity=quantity,
        execution_cost=spot_cost + futures_cost,
        spot_cash_transfer=spot_gross - spot_cost,
    )


def up_resize(
    position: H5bPosition,
    *,
    spot_price: Decimal,
    perp_price: Decimal,
    leverage: Decimal,
    cost_model: CostModel,
) -> ResizeResult | None:
    available = position.quantity_cap - position.quantity
    if available <= 0:
        return None

    equity = margin_equity(position, perp_price=perp_price)
    target = target_margin(
        quantity=position.quantity,
        perp_price=perp_price,
        leverage=leverage,
    )
    denominator = (
        spot_price
        * (Decimal("1") + cost_model.spot_execution_rate)
        + perp_price * cost_model.futures_execution_rate
        + perp_price / leverage
    )
    if denominator <= 0:
        raise RuntimeError("Invalid H5b up-resize denominator")

    requested = (equity - target) / denominator
    quantity = min(requested, available)
    if quantity <= 0:
        raise RuntimeError("Invalid H5b up-resize quantity")

    old_quantity = position.quantity
    old_entry = position.short_entry_price

    spot_gross = quantity * spot_price
    spot_cost = spot_gross * cost_model.spot_execution_rate
    futures_notional = quantity * perp_price
    futures_cost = (
        futures_notional * cost_model.futures_execution_rate
    )

    position.margin_cash -= (
        spot_gross
        + spot_cost
        + futures_cost
    )
    position.quantity += quantity
    position.short_entry_price = (
        old_quantity * old_entry + quantity * perp_price
    ) / position.quantity

    return ResizeResult(
        side="up",
        quantity=quantity,
        execution_cost=spot_cost + futures_cost,
        spot_cash_transfer=-(spot_gross + spot_cost),
    )


def _portfolio_equity(
    position: H5bPosition,
    *,
    spot_price: Decimal,
    perp_price: Decimal,
) -> Decimal:
    return (
        position.quantity * spot_price
        + margin_equity(position, perp_price=perp_price)
    )


def _close_position(
    position: H5bPosition,
    *,
    spot_price: Decimal,
    perp_price: Decimal,
    cost_model: CostModel,
) -> tuple[Decimal, Decimal]:
    spot_gross = position.quantity * spot_price
    spot_cost = spot_gross * cost_model.spot_execution_rate
    futures_notional = position.quantity * perp_price
    futures_cost = (
        futures_notional * cost_model.futures_execution_rate
    )
    realized_short_pnl = position.quantity * (
        position.short_entry_price - perp_price
    )
    ending_equity = (
        position.margin_cash
        + realized_short_pnl
        + spot_gross
        - spot_cost
        - futures_cost
    )
    return ending_equity, spot_cost + futures_cost


def simulate_h5b(
    *,
    leverage: Decimal,
    spot_prices: list[PricePoint],
    perp_prices: list[PricePoint],
    funding: list[FundingPoint],
    cost_model: CostModel,
) -> H5bSimulationResult:
    if leverage not in (Decimal("1"), Decimal("2")):
        raise ValueError("H5b supports only 1x or 2x leverage")
    if not spot_prices or not perp_prices:
        raise ValueError("H5b requires spot and perpetual prices")

    first_time = max(
        spot_prices[0].timestamp,
        perp_prices[0].timestamp,
    )
    end_time = min(
        spot_prices[-1].timestamp,
        perp_prices[-1].timestamp,
    )
    if end_time <= first_time:
        raise ValueError("H5b requires a positive simulation window")

    spot_cursor = 0
    perp_cursor = 0
    spot_entry, spot_cursor = _latest_price_at_or_before(
        spot_prices,
        first_time,
        cursor=spot_cursor,
    )
    perp_entry, perp_cursor = _latest_price_at_or_before(
        perp_prices,
        first_time,
        cursor=perp_cursor,
    )
    if not same_hour_tradable(spot_entry, perp_entry):
        raise ValueError("H5b entry requires matching spot/perp candle hours")

    quantity_cap = INITIAL_SPOT_NOTIONAL / spot_entry.close
    initial_short_notional = quantity_cap * perp_entry.close
    initial_margin = initial_short_notional / leverage
    starting_capital = INITIAL_SPOT_NOTIONAL + initial_margin

    entry_cost = (
        INITIAL_SPOT_NOTIONAL * cost_model.spot_execution_rate
        + initial_short_notional * cost_model.futures_execution_rate
    )
    position = H5bPosition(
        quantity=quantity_cap,
        quantity_cap=quantity_cap,
        short_entry_price=perp_entry.close,
        margin_cash=initial_margin - entry_cost,
    )

    funding_income = Decimal("0")
    trading_costs = entry_cost
    down_resizes = 0
    up_resizes = 0
    resized_btc = Decimal("0")
    minimum_margin_equity = margin_equity(
        position,
        perp_price=perp_entry.close,
    )
    equity_peak = starting_capital
    max_drawdown = Decimal("0")
    yearly_end_equity: dict[int, Decimal] = {}

    funding_by_time: dict[datetime, list[FundingPoint]] = {}
    for item in funding:
        if first_time <= item.timestamp <= end_time:
            funding_by_time.setdefault(item.timestamp, []).append(item)

    timeline = sorted(
        {
            point.timestamp
            for point in perp_prices
            if first_time <= point.timestamp <= end_time
        }
        | set(funding_by_time)
    )

    for timestamp in timeline:
        spot_point, spot_cursor = _latest_price_at_or_before(
            spot_prices,
            timestamp,
            cursor=spot_cursor,
        )
        perp_point, perp_cursor = _latest_price_at_or_before(
            perp_prices,
            timestamp,
            cursor=perp_cursor,
        )

        for funding_point in funding_by_time.get(timestamp, []):
            payment = (
                position.quantity
                * perp_point.close
                * funding_point.funding_rate
            )
            position.margin_cash += payment
            funding_income += payment

        current_margin_equity = margin_equity(
            position,
            perp_price=perp_point.close,
        )
        minimum_margin_equity = min(
            minimum_margin_equity,
            current_margin_equity,
        )

        ratio = coverage_ratio(
            position,
            perp_price=perp_point.close,
            leverage=leverage,
        )
        tradable = same_hour_tradable(
            spot_point,
            perp_point,
        )

        resize: ResizeResult | None = None
        if tradable and ratio < DOWN_TRIGGER:
            resize = down_resize(
                position,
                spot_price=spot_point.close,
                perp_price=perp_point.close,
                leverage=leverage,
                cost_model=cost_model,
            )
            down_resizes += 1
        elif (
            tradable
            and ratio > UP_TRIGGER
            and position.quantity < position.quantity_cap
        ):
            resize = up_resize(
                position,
                spot_price=spot_point.close,
                perp_price=perp_point.close,
                leverage=leverage,
                cost_model=cost_model,
            )
            if resize is not None:
                up_resizes += 1

        if resize is not None:
            trading_costs += resize.execution_cost
            resized_btc += resize.quantity

        equity = _portfolio_equity(
            position,
            spot_price=spot_point.close,
            perp_price=perp_point.close,
        )
        equity_peak = max(equity_peak, equity)
        if equity_peak > 0:
            drawdown = (
                (equity_peak - equity)
                / equity_peak
                * Decimal("100")
            )
            max_drawdown = max(max_drawdown, drawdown)

        yearly_end_equity[timestamp.year] = equity

    final_spot, _ = _latest_price_at_or_before(
        spot_prices,
        end_time,
        cursor=spot_cursor,
    )
    final_perp, _ = _latest_price_at_or_before(
        perp_prices,
        end_time,
        cursor=perp_cursor,
    )
    ending_equity, exit_cost = _close_position(
        position,
        spot_price=final_spot.close,
        perp_price=final_perp.close,
        cost_model=cost_model,
    )
    trading_costs += exit_cost

    # Frozen H5b accounting: the final calendar year includes exit costs.
    yearly_end_equity[end_time.year] = ending_equity

    yearly_returns: dict[int, Decimal] = {}
    previous_end: Decimal | None = None
    for year in sorted(yearly_end_equity):
        start_equity = (
            starting_capital
            if previous_end is None
            else previous_end
        )
        end_equity = yearly_end_equity[year]
        yearly_returns[year] = (
            (end_equity / start_equity - Decimal("1"))
            * Decimal("100")
        )
        previous_end = end_equity

    worst_rise = worst_24h_perp_rise_pct(
        [
            item
            for item in perp_prices
            if first_time <= item.timestamp <= end_time
        ]
    )
    static_stress_survives = (
        worst_rise / Decimal("100") * leverage
        < Decimal("1")
    )

    return H5bSimulationResult(
        leverage=leverage,
        cost_model=cost_model.name,
        starting_capital=starting_capital,
        ending_equity=ending_equity,
        net_return_pct=(
            (ending_equity / starting_capital - Decimal("1"))
            * Decimal("100")
        ),
        annualized_return_pct=_annualized_return(
            starting=starting_capital,
            ending=ending_equity,
            start=first_time,
            end=end_time,
        ),
        funding_income=funding_income,
        trading_costs=trading_costs,
        down_resizes=down_resizes,
        up_resizes=up_resizes,
        resized_btc=resized_btc,
        max_drawdown_pct=max_drawdown,
        minimum_margin_equity=minimum_margin_equity,
        worst_24h_perp_rise_pct=worst_rise,
        static_margin_stress_survives=static_stress_survives,
        yearly_returns_pct=yearly_returns,
    )
