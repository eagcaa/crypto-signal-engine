import asyncio
from datetime import UTC, datetime, timedelta

from crypto_signal_engine.calibration import (
    ReplayCalibrator,
    calibration_artifact_rejection_reason,
    load_calibration_artifact,
)
from crypto_signal_engine.collectors.binance import (
    BinanceDerivativesClient,
    BinanceFuturesTradeCollector,
    BinanceLiquidationCollector,
    BinanceSpotCandleClient,
    BinanceSpotOrderBookCollector,
    BinanceSpotTradeCollector,
)
from crypto_signal_engine.collectors.bybit import (
    BybitDerivativesClient,
    BybitFuturesTradeCollector,
    BybitLiquidationCollector,
    BybitSpotOrderBookCollector,
    BybitSpotTradeCollector,
)
from crypto_signal_engine.config.settings import get_settings
from crypto_signal_engine.db import (
    CoinGlassSnapshotRepository,
    DerivativesRepository,
    MarketSnapshotRepository,
    PaperPositionRepository,
    PredictionRepository,
    ResearchFeatureRepository,
    create_database_engine,
    create_session_factory,
    initialize_database,
)
from crypto_signal_engine.domain.models import Exchange
from crypto_signal_engine.features.orderbook import calculate_order_book_metrics
from crypto_signal_engine.features.research import ResearchFeatureAggregator
from crypto_signal_engine.features.technical import build_technical_features
from crypto_signal_engine.health import DataQualityMonitor, SourceFreshnessMonitor
from crypto_signal_engine.integrations import (
    CoinGlassClient,
    TelegramDispatcher,
    TelegramNotifier,
)
from crypto_signal_engine.integrations.coinglass import CoinGlassApiError
from crypto_signal_engine.market import MarketSnapshotAggregator
from crypto_signal_engine.paper import (
    CandidatePaperTracker,
    PaperBroker,
    PaperRiskConfig,
    build_paper_performance_report,
)
from crypto_signal_engine.predictions import (
    CompositePredictionEngine,
    LiveFirstTouchEvaluator,
)


async def send_telegram(
    dispatcher: TelegramDispatcher | None,
    text: str,
) -> None:
    if dispatcher is None:
        return

    if not dispatcher.enqueue(text):
        print("TELEGRAM queue unavailable/full; alert dropped")


def print_paper_position(position, *, event: str) -> None:
    if position is None:
        return

    if event == "open":
        print(
            "PAPER_OPEN "
            f"id={position.id} "
            f"prediction_id={position.prediction_id} "
            f"symbol={position.symbol} "
            f"horizon={position.horizon_seconds}s "
            f"direction={position.direction} "
            f"entry={position.entry_price} "
            f"notional={position.notional:.2f} "
            f"quantity={position.quantity:.8f}"
        )
        return

    print(
        "PAPER_CLOSE "
        f"id={position.id} "
        f"prediction_id={position.prediction_id} "
        f"status={position.status.value} "
        f"exit={position.exit_price} "
        f"return={position.return_pct} "
        f"pnl={position.pnl} "
        f"reason={position.close_reason}"
    )


def print_evaluation(evaluation, *, source: str) -> None:
    if evaluation.return_pct is None:
        print(
            "EVALUATED "
            f"source={source} "
            f"id={evaluation.prediction_id} "
            f"status={evaluation.status.value} "
            f"outcome={evaluation.outcome.value} "
            f"label={evaluation.label} "
            f"eval_source={evaluation.evaluation_source} "
            f"eval_version={evaluation.evaluation_version}"
        )
    else:
        print(
            "EVALUATED "
            f"source={source} "
            f"id={evaluation.prediction_id} "
            f"status={evaluation.status.value} "
            f"outcome={evaluation.outcome.value} "
            f"label={evaluation.label} "
            f"return={evaluation.return_pct:.4f}% "
            f"success={evaluation.success} "
            f"eval_source={evaluation.evaluation_source} "
            f"eval_version={evaluation.evaluation_version}"
        )


async def consume_trades(
    collector,
    aggregator: MarketSnapshotAggregator,
    research_aggregator: ResearchFeatureAggregator,
    live_evaluator: LiveFirstTouchEvaluator,
    prediction_repository: PredictionRepository,
    paper_broker: PaperBroker | None = None,
    paper_repository: PaperPositionRepository | None = None,
    candidate_paper_tracker: CandidatePaperTracker | None = None,
    telegram_dispatcher: TelegramDispatcher | None = None,
) -> None:
    async for trade in collector.trades():
        await aggregator.update_trade(trade)
        await research_aggregator.update_trade(trade)

        evaluations = await live_evaluator.process_trade(trade)
        for evaluation in evaluations:
            if await prediction_repository.add_evaluation(evaluation):
                print_evaluation(evaluation, source="tick")
                if paper_broker is not None:
                    paper_position = paper_broker.apply_evaluation(
                        evaluation
                    )
                    if (
                        paper_position is not None
                        and paper_repository is not None
                    ):
                        await paper_repository.update(paper_position)
                    print_paper_position(
                        paper_position,
                        event="close",
                    )
                    if paper_position is not None:
                        await send_telegram(
                            telegram_dispatcher,
                            TelegramNotifier.paper_account_text(
                                paper_broker.snapshot(),
                                position=paper_position,
                            ),
                        )
                if candidate_paper_tracker is not None:
                    candidate_position = (
                        candidate_paper_tracker.broker.apply_evaluation(
                            evaluation
                        )
                    )
                    if (
                        candidate_position is not None
                        and paper_repository is not None
                    ):
                        await paper_repository.update(candidate_position)
                    if candidate_position is not None:
                        print(
                            "CANDIDATE_PAPER_CLOSE "
                            f"model={candidate_position.model_name} "
                            f"prediction_id={candidate_position.prediction_id} "
                            f"return={candidate_position.return_pct} "
                            f"pnl={candidate_position.pnl} "
                            f"reason={candidate_position.close_reason}"
                        )
                        await send_telegram(
                            telegram_dispatcher,
                            TelegramNotifier.candidate_close_text(
                                candidate_position,
                                candidate_name=candidate_paper_tracker.gate.name,
                            ),
                        )


async def consume_order_book(
    exchange: Exchange,
    collector,
    aggregator: MarketSnapshotAggregator,
) -> None:
    async for snapshot in collector.snapshots():
        metrics = calculate_order_book_metrics(snapshot, depth_levels=20)
        await aggregator.update_order_book(
            exchange,
            metrics,
            event_time=snapshot.event_time,
        )






async def poll_technicals(
    client: BinanceSpotCandleClient,
    research_aggregator: ResearchFeatureAggregator,
    *,
    symbol: str,
    interval_seconds: float = 30.0,
) -> None:
    while True:
        try:
            candles_5m, candles_15m, candles_1h, candles_4h = await asyncio.gather(
                client.fetch_closed(symbol, interval="5m", limit=100),
                client.fetch_closed(symbol, interval="15m", limit=100),
                client.fetch_closed(symbol, interval="1h", limit=100),
                client.fetch_closed(symbol, interval="4h", limit=100),
            )
            features_5m = build_technical_features(candles_5m)
            features_15m = build_technical_features(candles_15m)
            features_1h = build_technical_features(candles_1h)
            features_4h = build_technical_features(candles_4h)

            for features in (
                features_5m,
                features_15m,
                features_1h,
                features_4h,
            ):
                await research_aggregator.update_technical(features)

            for features in (features_5m, features_15m, features_1h, features_4h):
                print(
                    "TECHNICALS "
                    f"interval={features.interval} "
                    f"close={features.close} "
                    f"ema9={features.ema_fast:.4f} "
                    f"ema21={features.ema_slow:.4f} "
                    f"trend_score={features.trend_score:+.4f} "
                    f"trend={features.trend_regime} "
                    f"atr={features.atr:.4f} "
                    f"atr_pct={features.atr_pct:.4f}% "
                    f"volatility={features.volatility_regime}"
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"TECHNICALS unavailable: {type(exc).__name__}: {exc}")

        await asyncio.sleep(interval_seconds)


async def poll_derivatives(
    client,
    repository: DerivativesRepository,
    research_aggregator: ResearchFeatureAggregator,
    *,
    symbol: str,
    interval_seconds: float = 30.0,
) -> None:
    while True:
        try:
            snapshot = await client.fetch_snapshot(symbol)
            await repository.add_snapshot(snapshot)
            await research_aggregator.update_derivatives(snapshot)

            print(
                "DERIVATIVES "
                f"exchange={snapshot.exchange.value} "
                f"oi={snapshot.open_interest} "
                f"oi_usd={snapshot.open_interest_value_usd} "
                f"oi_5m={snapshot.oi_change_5m_pct}% "
                f"oi_15m={snapshot.oi_change_15m_pct}% "
                f"funding={snapshot.funding_rate} "
                f"long_short={snapshot.long_short_ratio} "
                f"top_trader={snapshot.top_trader_long_short_ratio} "
                f"taker_buy_sell={snapshot.taker_buy_sell_ratio}"
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"DERIVATIVES unavailable: {type(client).__name__}: {exc}")

        await asyncio.sleep(interval_seconds)


async def consume_liquidations(
    collector,
    repository: DerivativesRepository,
    research_aggregator: ResearchFeatureAggregator,
) -> None:
    async for event in collector.events():
        await repository.add_liquidation(event)
        await research_aggregator.update_liquidation(event)
        print(
            "LIQUIDATION "
            f"exchange={event.exchange.value} "
            f"symbol={event.symbol} "
            f"side={event.position_side.value} "
            f"notional_usd={event.notional_usd:.2f} "
            f"price={event.price}"
        )


async def poll_coinglass(
    client: CoinGlassClient,
    repository: CoinGlassSnapshotRepository,
    *,
    symbol: str,
    interval_seconds: float = 20.0,
) -> None:
    while True:
        try:
            snapshot = await client.fetch_market_snapshot(symbol=symbol)
            await repository.add(snapshot)

            print(
                "COINGLASS "
                f"oi_usd={snapshot.open_interest_usd} "
                f"oi_5m={snapshot.oi_change_5m_pct}% "
                f"oi_15m={snapshot.oi_change_15m_pct}% "
                f"funding_binance={snapshot.funding_rate_binance} "
                f"funding_bybit={snapshot.funding_rate_bybit} "
                f"taker_buy={snapshot.taker_buy_ratio}% "
                f"taker_sell={snapshot.taker_sell_ratio}%"
            )
        except (CoinGlassApiError, TimeoutError) as exc:
            print(f"COINGLASS unavailable: {exc}")
        except Exception as exc:
            print(f"COINGLASS unexpected error: {exc}")

        await asyncio.sleep(interval_seconds)


async def persist_snapshots(
    aggregator: MarketSnapshotAggregator,
    snapshot_repository: MarketSnapshotRepository,
    prediction_repository: PredictionRepository,
    research_repository: ResearchFeatureRepository,
    research_aggregator: ResearchFeatureAggregator,
    prediction_engine: CompositePredictionEngine,
    live_evaluator: LiveFirstTouchEvaluator,
    paper_broker: PaperBroker | None = None,
    paper_repository: PaperPositionRepository | None = None,
    candidate_paper_tracker: CandidatePaperTracker | None = None,
    telegram_dispatcher: TelegramDispatcher | None = None,
    calibration_buckets=(),
    data_quality_monitor: DataQualityMonitor | None = None,
    source_freshness_monitor: SourceFreshnessMonitor | None = None,
    *,
    interval_seconds: float = 5.0,
    prediction_interval_seconds: int = 60,
) -> None:
    last_prediction_at = None
    horizons = (300, 900, 3600)

    while True:
        await asyncio.sleep(interval_seconds)
        snapshot = await aggregator.snapshot()

        await snapshot_repository.add(snapshot)

        if data_quality_monitor is not None:
            quality_event = data_quality_monitor.observe(snapshot)
            if quality_event is not None:
                print(
                    "DATA_QUALITY "
                    f"event={quality_event.kind} "
                    f"quality={quality_event.data_quality:.2f} "
                    f"bad_intervals={quality_event.bad_intervals}"
                )
                await send_telegram(
                    telegram_dispatcher,
                    TelegramNotifier.data_quality_text(
                        symbol=snapshot.symbol,
                        kind=quality_event.kind,
                        data_quality=quality_event.data_quality,
                        bad_intervals=quality_event.bad_intervals,
                    ),
                )

        if source_freshness_monitor is not None:
            for freshness_event in source_freshness_monitor.observe(snapshot):
                print(
                    "SOURCE_FRESHNESS "
                    f"source={freshness_event.source} "
                    f"event={freshness_event.kind} "
                    f"age_ms={freshness_event.age_ms} "
                    f"bad_intervals={freshness_event.bad_intervals}"
                )
                await send_telegram(
                    telegram_dispatcher,
                    TelegramNotifier.source_freshness_text(
                        symbol=snapshot.symbol,
                        source=freshness_event.source,
                        kind=freshness_event.kind,
                        age_ms=freshness_event.age_ms,
                        bad_intervals=freshness_event.bad_intervals,
                    ),
                )

        research_snapshot = await research_aggregator.snapshot(snapshot)
        await research_repository.add(research_snapshot)

        print(
            "FEATURES "
            f"spot_cvd_1m={research_snapshot.spot_cvd_1m} "
            f"spot_cvd_5m={research_snapshot.spot_cvd_5m} "
            f"spot_cvd_15m={research_snapshot.spot_cvd_15m} "
            f"futures_cvd_1m={research_snapshot.futures_cvd_1m} "
            f"futures_cvd_5m={research_snapshot.futures_cvd_5m} "
            f"futures_cvd_15m={research_snapshot.futures_cvd_15m} "
            f"spot_ratio_1m={research_snapshot.spot_cvd_ratio_1m:.4f} "
            f"spot_ratio_5m={research_snapshot.spot_cvd_ratio_5m:.4f} "
            f"spot_ratio_15m={research_snapshot.spot_cvd_ratio_15m:.4f} "
            f"futures_ratio_1m={research_snapshot.futures_cvd_ratio_1m:.4f} "
            f"futures_ratio_5m={research_snapshot.futures_cvd_ratio_5m:.4f} "
            f"futures_ratio_15m={research_snapshot.futures_cvd_ratio_15m:.4f} "
            f"trade_sources={research_snapshot.spot_trade_sources}/"
            f"{research_snapshot.futures_trade_sources} "
            f"history={research_snapshot.history_seconds}s "
            f"trend_5m={research_snapshot.trend_score_5m} "
            f"trend_15m={research_snapshot.trend_score_15m} "
            f"trend_1h={research_snapshot.trend_score_1h} "
            f"trend_4h={research_snapshot.trend_score_4h} "
            f"atr_pct_5m={research_snapshot.atr_pct_5m} "
            f"atr_pct_15m={research_snapshot.atr_pct_15m} "
            f"atr_pct_1h={research_snapshot.atr_pct_1h} "
            f"atr_pct_4h={research_snapshot.atr_pct_4h} "
            f"liq_5m={research_snapshot.liquidation_imbalance_5m} "
            f"liq_15m={research_snapshot.liquidation_imbalance_15m} "
            f"binance_oi_5m={research_snapshot.binance_oi_change_5m_pct} "
            f"binance_oi_15m={research_snapshot.binance_oi_change_15m_pct} "
            f"bybit_oi_5m={research_snapshot.bybit_oi_change_5m_pct} "
            f"bybit_oi_15m={research_snapshot.bybit_oi_change_15m_pct}"
        )

        evaluations = await prediction_repository.evaluate_due(snapshot.timestamp)
        for evaluation in evaluations:
            print_evaluation(evaluation, source="snapshot")
            if paper_broker is not None:
                paper_position = paper_broker.apply_evaluation(
                    evaluation
                )
                if (
                    paper_position is not None
                    and paper_repository is not None
                ):
                    await paper_repository.update(paper_position)
                print_paper_position(
                    paper_position,
                    event="close",
                )
                if paper_position is not None:
                    await send_telegram(
                        telegram_dispatcher,
                        TelegramNotifier.paper_account_text(
                            paper_broker.snapshot(),
                            position=paper_position,
                        ),
                    )
            if candidate_paper_tracker is not None:
                candidate_position = (
                    candidate_paper_tracker.broker.apply_evaluation(
                        evaluation
                    )
                )
                if (
                    candidate_position is not None
                    and paper_repository is not None
                ):
                    await paper_repository.update(candidate_position)
                if candidate_position is not None:
                    print(
                        "CANDIDATE_PAPER_CLOSE "
                        f"model={candidate_position.model_name} "
                        f"prediction_id={candidate_position.prediction_id} "
                        f"return={candidate_position.return_pct} "
                        f"pnl={candidate_position.pnl} "
                        f"reason={candidate_position.close_reason}"
                    )
                    await send_telegram(
                        telegram_dispatcher,
                        TelegramNotifier.candidate_close_text(
                            candidate_position,
                            candidate_name=candidate_paper_tracker.gate.name,
                        ),
                    )

        should_generate = (
            last_prediction_at is None
            or snapshot.timestamp - last_prediction_at
            >= timedelta(seconds=prediction_interval_seconds)
        )

        if should_generate:
            for horizon_seconds in horizons:
                decision = prediction_engine.decide(
                    research_snapshot,
                    horizon_seconds=horizon_seconds,
                )

                contribution_text = " ".join(
                    f"{name}={value:+.3f}"
                    for name, value in decision.feature_contributions.items()
                )

                if decision.prediction is None:
                    print(
                        "DECISION "
                        f"horizon={decision.horizon_seconds}s "
                        f"direction={decision.direction.value} "
                        f"raw_score={decision.raw_score:+.4f} "
                        f"{contribution_text} "
                        f"reason={decision.reason}"
                    )
                else:
                    prediction = decision.prediction
                    await prediction_repository.add(prediction)
                    await live_evaluator.register(prediction)

                    is_shadow = prediction_engine.is_shadow_horizon(
                        prediction.horizon_seconds
                    )

                    if paper_broker is not None and not is_shadow:
                        paper_position = paper_broker.open_from_prediction(
                            prediction
                        )
                        if paper_position is not None:
                            if paper_repository is not None:
                                await paper_repository.add(
                                    paper_position
                                )
                            print_paper_position(
                                paper_position,
                                event="open",
                            )
                        else:
                            paper_snapshot = paper_broker.snapshot()
                            reason = (
                                paper_snapshot.halt_reason
                                if paper_snapshot.trading_halted
                                else "risk_or_position_limit"
                            )
                            print(
                                "PAPER_SKIP "
                                f"prediction_id={prediction.id} "
                                f"reason={reason}"
                            )

                    if candidate_paper_tracker is not None and not is_shadow:
                        candidate_prediction = (
                            candidate_paper_tracker.candidate_prediction(
                                prediction,
                                research_snapshot,
                            )
                        )
                        if candidate_prediction is not None:
                            await prediction_repository.add(
                                candidate_prediction
                            )
                            await live_evaluator.register(
                                candidate_prediction
                            )
                            candidate_position = (
                                candidate_paper_tracker.broker
                                .open_from_prediction(candidate_prediction)
                            )
                            if candidate_position is not None:
                                if paper_repository is not None:
                                    await paper_repository.add(
                                        candidate_position
                                    )
                                print(
                                    "CANDIDATE_PAPER_OPEN "
                                    f"candidate={candidate_paper_tracker.gate.name} "
                                    f"model={candidate_prediction.model_name} "
                                    f"prediction_id={candidate_prediction.id} "
                                    f"entry={candidate_prediction.entry_price} "
                                    f"tp={candidate_prediction.take_profit_pct:.4f}% "
                                    f"sl={candidate_prediction.stop_loss_pct:.4f}%"
                                )
                                await send_telegram(
                                    telegram_dispatcher,
                                    TelegramNotifier.candidate_open_text(
                                        candidate_prediction,
                                        candidate_name=candidate_paper_tracker.gate.name,
                                    ),
                                )
                            else:
                                candidate_snapshot = (
                                    candidate_paper_tracker.broker.snapshot()
                                )
                                reason = (
                                    candidate_snapshot.halt_reason
                                    if candidate_snapshot.trading_halted
                                    else "risk_or_position_limit"
                                )
                                print(
                                    "CANDIDATE_PAPER_SKIP "
                                    f"candidate={candidate_paper_tracker.gate.name} "
                                    f"prediction_id={candidate_prediction.id} "
                                    f"reason={reason}"
                                )

                    if is_shadow:
                        print(
                            "SHADOW_PREDICTION "
                            f"id={prediction.id} "
                            f"horizon={prediction.horizon_seconds}s "
                            f"direction={prediction.direction.value} "
                            f"raw_score={prediction.raw_score:+.4f} "
                            f"entry={prediction.entry_price} "
                            f"tp={prediction.take_profit_pct:.4f}% "
                            f"sl={prediction.stop_loss_pct:.4f}% "
                            f"{contribution_text} "
                            f"reason={prediction.reason}"
                        )
                        continue

                    calibrated_confidence = (
                        ReplayCalibrator().confidence_for_prediction(
                            prediction,
                            calibration_buckets,
                        )
                        if calibration_buckets
                        else None
                    )

                    await send_telegram(
                        telegram_dispatcher,
                        TelegramNotifier.prediction_text(
                            prediction,
                            calibrated_confidence=calibrated_confidence,
                        ),
                    )

                    confidence_text = (
                        "not_ready"
                        if calibrated_confidence is None
                        else f"{calibrated_confidence:.2f}%"
                    )

                    print(
                        "PREDICTION "
                        f"id={prediction.id} "
                        f"horizon={prediction.horizon_seconds}s "
                        f"direction={prediction.direction.value} "
                        f"raw_score={prediction.raw_score:+.4f} "
                        f"confidence={confidence_text} "
                        f"entry={prediction.entry_price} "
                        f"tp={prediction.take_profit_pct:.4f}% "
                        f"sl={prediction.stop_loss_pct:.4f}% "
                        f"{contribution_text} "
                        f"reason={prediction.reason}"
                    )

            if paper_broker is not None:
                paper_snapshot = paper_broker.snapshot()
                win_rate = (
                    f"{paper_snapshot.win_rate:.2f}%"
                    if paper_snapshot.win_rate is not None
                    else "n/a"
                )
                current_model_names = tuple(
                    model_name
                    for _, model_name
                    in CompositePredictionEngine.current_model_names()
                )
                performance = build_paper_performance_report(
                    list(paper_broker.positions),
                    model_names=current_model_names,
                ).overall
                profit_factor = (
                    "n/a"
                    if performance.profit_factor is None
                    else f"{performance.profit_factor:.3f}"
                )
                expectancy = (
                    "n/a"
                    if performance.expectancy is None
                    else f"{performance.expectancy:+.4f}"
                )
                print(
                    "PAPER_ACCOUNT "
                    f"equity={paper_snapshot.equity:.2f} "
                    f"realized_pnl={paper_snapshot.realized_pnl:+.2f} "
                    f"wins={paper_snapshot.wins} "
                    f"losses={paper_snapshot.losses} "
                    f"win_rate={win_rate} "
                    f"execution_costs={performance.execution_costs:.2f} "
                    f"profit_factor={profit_factor} "
                    f"expectancy={expectancy} "
                    f"drawdown={paper_snapshot.drawdown_pct:.4f}% "
                    f"max_drawdown={paper_snapshot.max_drawdown_pct:.4f}% "
                    f"consecutive_losses={paper_snapshot.consecutive_losses} "
                    f"open={paper_snapshot.open_positions} "
                    f"halted={paper_snapshot.trading_halted} "
                    f"halt_reason={paper_snapshot.halt_reason}"
                )

            last_prediction_at = snapshot.timestamp

        print()
        print(f"{snapshot.symbol} | {snapshot.timestamp.isoformat()}")
        print(f"price={snapshot.price}")
        print(
            "cvd "
            f"binance_spot={snapshot.binance_spot_cvd} "
            f"binance_futures={snapshot.binance_futures_cvd} "
            f"bybit_spot={snapshot.bybit_spot_cvd} "
            f"bybit_futures={snapshot.bybit_futures_cvd}"
        )
        print(
            "flow "
            f"spot_total={snapshot.spot_cvd_total} "
            f"futures_total={snapshot.futures_cvd_total} "
            f"divergence={snapshot.spot_futures_divergence}"
        )
        print(
            "book "
            f"binance={snapshot.binance_book_imbalance} "
            f"bybit={snapshot.bybit_book_imbalance} "
            f"cross_exchange_div={snapshot.cross_exchange_book_divergence}"
        )
        print(
            "pressure "
            f"buy={snapshot.buy_pressure} "
            f"sell={snapshot.sell_pressure}"
        )
        print(
            "freshness_ms "
            f"binance_spot={snapshot.binance_spot_age_ms} "
            f"binance_futures={snapshot.binance_futures_age_ms} "
            f"bybit_spot={snapshot.bybit_spot_age_ms} "
            f"bybit_futures={snapshot.bybit_futures_age_ms} "
            f"binance_book={snapshot.binance_book_age_ms} "
            f"bybit_book={snapshot.bybit_book_age_ms}"
        )
        print(f"data_quality={snapshot.data_quality:.2f} persisted=yes")


async def main() -> None:
    settings = get_settings()
    engine = create_database_engine(settings.database_url)

    await initialize_database(engine)

    session_factory = create_session_factory(engine)
    snapshot_repository = MarketSnapshotRepository(session_factory)
    prediction_repository = PredictionRepository(session_factory)
    paper_repository = PaperPositionRepository(session_factory)
    coinglass_repository = CoinGlassSnapshotRepository(session_factory)
    derivatives_repository = DerivativesRepository(session_factory)
    research_repository = ResearchFeatureRepository(session_factory)
    prediction_engine = CompositePredictionEngine(
        fee_pct_per_side=settings.paper_fee_pct_per_side,
        slippage_pct_per_side=settings.paper_slippage_pct_per_side,
    )
    live_evaluator = LiveFirstTouchEvaluator()
    paper_broker = None
    candidate_paper_tracker = None
    telegram_notifier = None
    telegram_dispatcher = None
    data_quality_monitor = DataQualityMonitor(
        minimum_quality=settings.data_quality_alert_threshold,
        bad_intervals_before_alert=settings.data_quality_bad_intervals,
    )
    source_freshness_monitor = SourceFreshnessMonitor(
        stale_after_ms=settings.source_stale_after_ms,
        bad_intervals_before_alert=settings.source_stale_bad_intervals,
    )
    calibration_artifact = load_calibration_artifact(
        settings.calibration_file
    )
    calibration_buckets = ()

    calibration_rejection = calibration_artifact_rejection_reason(
        calibration_artifact,
        now=datetime.now(UTC),
        maximum_age=timedelta(
            hours=settings.calibration_max_age_hours
        ),
        symbol="BTCUSDT",
    )

    if calibration_rejection is None and calibration_artifact is not None:
        calibration_buckets = calibration_artifact.buckets
        ready_buckets = sum(
            1
            for bucket in calibration_buckets
            if bucket.calibrated_confidence is not None
        )
        print(
            "CALIBRATION loaded "
            f"path={settings.calibration_file} "
            f"source={calibration_artifact.price_source} "
            f"generated_at={calibration_artifact.generated_at.isoformat()} "
            f"buckets={len(calibration_buckets)} "
            f"ready={ready_buckets}"
        )
    else:
        print(
            "CALIBRATION rejected "
            f"path={settings.calibration_file} "
            f"reason={calibration_rejection}"
        )

    if settings.telegram_enabled:
        if settings.telegram_bot_token and settings.telegram_chat_id:
            telegram_notifier = TelegramNotifier(
                settings.telegram_bot_token,
                settings.telegram_chat_id,
            )
            telegram_dispatcher = TelegramDispatcher(
                telegram_notifier
            )
            await telegram_dispatcher.start()
            print("TELEGRAM enabled")
        else:
            print(
                "TELEGRAM enabled but token/chat id is empty; "
                "notifications disabled."
            )

    if settings.paper_trading_enabled:
        paper_broker = PaperBroker(
            PaperRiskConfig(
                starting_equity=settings.paper_starting_equity,
                risk_per_trade_pct=settings.paper_risk_per_trade_pct,
                max_notional_pct=settings.paper_max_notional_pct,
                max_open_positions=settings.paper_max_open_positions,
                max_drawdown_pct=settings.paper_max_drawdown_pct,
                max_consecutive_losses=settings.paper_max_consecutive_losses,
                fee_pct_per_side=settings.paper_fee_pct_per_side,
                slippage_pct_per_side=settings.paper_slippage_pct_per_side,
            )
        )
        current_model_names = tuple(
            model_name
            for _, model_name
            in CompositePredictionEngine.current_model_names()
        )
        stored_paper_positions = await paper_repository.load_all(
            model_names=current_model_names
        )
        paper_broker.restore(stored_paper_positions)
        paper_snapshot = paper_broker.snapshot()
        print(
            "PAPER_ENABLED "
            f"equity={paper_snapshot.equity:.2f} "
            f"risk_per_trade={settings.paper_risk_per_trade_pct}% "
            f"max_notional={settings.paper_max_notional_pct}% "
            f"max_open_positions={settings.paper_max_open_positions} "
            f"max_drawdown={settings.paper_max_drawdown_pct}% "
            f"max_consecutive_losses={settings.paper_max_consecutive_losses} "
            f"fee_per_side={settings.paper_fee_pct_per_side}% "
            f"slippage_per_side={settings.paper_slippage_pct_per_side}% "
            f"restored_positions={len(stored_paper_positions)} "
            f"models={','.join(current_model_names)}"
        )

    if settings.candidate_paper_enabled:
        candidate_broker = PaperBroker(
            PaperRiskConfig(
                starting_equity=settings.paper_starting_equity,
                risk_per_trade_pct=settings.paper_risk_per_trade_pct,
                max_notional_pct=settings.paper_max_notional_pct,
                max_open_positions=1,
                max_drawdown_pct=settings.paper_max_drawdown_pct,
                max_consecutive_losses=settings.paper_max_consecutive_losses,
                fee_pct_per_side=settings.paper_fee_pct_per_side,
                slippage_pct_per_side=settings.paper_slippage_pct_per_side,
            )
        )
        candidate_paper_tracker = CandidatePaperTracker(
            candidate_broker,
            candidate_name=settings.candidate_paper_name,
        )
        stored_candidate_positions = await paper_repository.load_all(
            model_names=(candidate_paper_tracker.model_name,)
        )
        candidate_broker.restore(stored_candidate_positions)
        candidate_snapshot = candidate_broker.snapshot()
        print(
            "CANDIDATE_PAPER_ENABLED "
            f"candidate={candidate_paper_tracker.gate.name} "
            f"model={candidate_paper_tracker.model_name} "
            f"equity={candidate_snapshot.equity:.2f} "
            f"restored_positions={len(stored_candidate_positions)} "
            "exchange_orders=disabled"
        )

    symbols = ["BTCUSDT"]
    restored_predictions = await prediction_repository.load_open_predictions(
        now=datetime.now(UTC),
        symbols=symbols,
    )
    restored_count = await live_evaluator.register_many(restored_predictions)
    if restored_count:
        print(
            "RECOVERY "
            f"restored_open_predictions={restored_count} "
            f"symbols={','.join(symbols)}"
        )
    aggregator = MarketSnapshotAggregator("BTCUSDT")
    research_aggregator = ResearchFeatureAggregator("BTCUSDT")

    try:
        async with asyncio.TaskGroup() as task_group:
            task_group.create_task(
                consume_trades(
                    BinanceSpotTradeCollector(symbols),
                    aggregator,
                    research_aggregator,
                    live_evaluator,
                    prediction_repository,
                    paper_broker,
                    paper_repository,
                    candidate_paper_tracker,
                    telegram_dispatcher,
                )
            )
            task_group.create_task(
                consume_trades(
                    BinanceFuturesTradeCollector(symbols),
                    aggregator,
                    research_aggregator,
                    live_evaluator,
                    prediction_repository,
                )
            )
            task_group.create_task(
                consume_trades(
                    BybitSpotTradeCollector(symbols),
                    aggregator,
                    research_aggregator,
                    live_evaluator,
                    prediction_repository,
                )
            )
            task_group.create_task(
                consume_trades(
                    BybitFuturesTradeCollector(symbols),
                    aggregator,
                    research_aggregator,
                    live_evaluator,
                    prediction_repository,
                )
            )
            task_group.create_task(
                consume_order_book(
                    Exchange.BINANCE,
                    BinanceSpotOrderBookCollector(
                        symbols,
                        depth=20,
                        update_ms=100,
                    ),
                    aggregator,
                )
            )
            task_group.create_task(
                consume_order_book(
                    Exchange.BYBIT,
                    BybitSpotOrderBookCollector(
                        symbols,
                        depth=50,
                    ),
                    aggregator,
                )
            )
            task_group.create_task(
                persist_snapshots(
                    aggregator,
                    snapshot_repository,
                    prediction_repository,
                    research_repository,
                    research_aggregator,
                    prediction_engine,
                    live_evaluator,
                    paper_broker,
                    paper_repository,
                    candidate_paper_tracker,
                    telegram_dispatcher,
                    calibration_buckets,
                    data_quality_monitor,
                    source_freshness_monitor,
                    interval_seconds=5.0,
                    prediction_interval_seconds=60,
                )
            )

            task_group.create_task(
                poll_technicals(
                    BinanceSpotCandleClient(),
                    research_aggregator,
                    symbol="BTCUSDT",
                    interval_seconds=30.0,
                )
            )

            task_group.create_task(
                poll_derivatives(
                    BinanceDerivativesClient(),
                    derivatives_repository,
                    research_aggregator,
                    symbol="BTCUSDT",
                    interval_seconds=30.0,
                )
            )
            task_group.create_task(
                poll_derivatives(
                    BybitDerivativesClient(),
                    derivatives_repository,
                    research_aggregator,
                    symbol="BTCUSDT",
                    interval_seconds=30.0,
                )
            )
            task_group.create_task(
                consume_liquidations(
                    BinanceLiquidationCollector(symbols),
                    derivatives_repository,
                    research_aggregator,
                )
            )
            task_group.create_task(
                consume_liquidations(
                    BybitLiquidationCollector(symbols),
                    derivatives_repository,
                    research_aggregator,
                )
            )

            if settings.coinglass_enabled and settings.coinglass_api_key:
                task_group.create_task(
                    poll_coinglass(
                        CoinGlassClient(settings.coinglass_api_key),
                        coinglass_repository,
                        symbol="BTCUSDT",
                        interval_seconds=20.0,
                    )
                )
            elif settings.coinglass_enabled:
                print(
                    "COINGLASS enabled but COINGLASS_API_KEY is empty; "
                    "integration disabled."
                )
    finally:
        if telegram_dispatcher is not None:
            await telegram_dispatcher.stop()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
