from dataclasses import dataclass

from crypto_signal_engine.replay.models import ReplayPricePoint, ReplayResult


@dataclass(frozen=True, slots=True)
class ResearchReplayInputs:
    result: ReplayResult
    price_points: list[ReplayPricePoint]
    price_source: str


def select_research_replay_inputs(
    *,
    primary_result: ReplayResult,
    primary_price_points: list[ReplayPricePoint],
    exact_result: ReplayResult | None,
    exact_price_points: list[ReplayPricePoint] | None,
    exact_binance_trades: bool,
    compare_price_sources: bool,
) -> ResearchReplayInputs:
    use_exact = exact_binance_trades or compare_price_sources

    if use_exact:
        if exact_price_points is None:
            raise ValueError(
                "Exact research replay requested but exact price points are unavailable."
            )
        selected_result = exact_result if exact_result is not None else primary_result
        return ResearchReplayInputs(
            result=selected_result,
            price_points=exact_price_points,
            price_source="binance_spot_aggTrades",
        )

    return ResearchReplayInputs(
        result=primary_result,
        price_points=primary_price_points,
        price_source="persisted_market_snapshots",
    )
