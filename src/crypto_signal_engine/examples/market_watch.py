"""Live market-watch alerts (context only, no predictions, no orders).

Streams Binance spot/futures trades and liquidations, polls Binance
derivatives once a minute, and prints/sends short Turkish alerts.
Runs independently of the research collector and needs no database.
"""

import argparse
import asyncio
import os
from datetime import UTC, datetime, timedelta

from crypto_signal_engine.collectors.binance import (
    BinanceDerivativesClient,
    BinanceFuturesTradeCollector,
    BinanceLiquidationCollector,
    BinanceSpotTradeCollector,
)
from crypto_signal_engine.config.settings import get_settings
from crypto_signal_engine.db import (
    RangeForecastRepository,
    create_database_engine,
    create_session_factory,
    initialize_database,
)
from crypto_signal_engine.integrations import (
    TelegramCommandPoller,
    TelegramDispatcher,
    TelegramNotifier,
)
from crypto_signal_engine.watch import MarketWatch, WatchAlert, WatchConfig
from crypto_signal_engine.watch.coin_analysis import CoinAnalyzer
from crypto_signal_engine.watch.range_forward import RangeForwardTracker


def emit(
    alerts: list[WatchAlert],
    dispatcher: TelegramDispatcher | None,
    *,
    send_event_alerts: bool = True,
) -> None:
    for alert in alerts:
        print(
            f"WATCH_ALERT kind={alert.kind} "
            f"time={alert.timestamp.isoformat()}\n{alert.text}\n"
        )
        should_send = send_event_alerts or alert.kind == "summary"
        if (
            dispatcher is not None
            and should_send
            and not dispatcher.enqueue(alert.text)
        ):
            print("TELEGRAM queue unavailable/full; alert dropped")


async def consume_trades(
    collector,
    watch,
    dispatcher,
    *,
    send_event_alerts: bool,
) -> None:
    async for trade in collector.trades():
        emit(
            watch.on_trade(trade),
            dispatcher,
            send_event_alerts=send_event_alerts,
        )


async def consume_liquidations(
    collector,
    watch,
    dispatcher,
    *,
    send_event_alerts: bool,
) -> None:
    async for event in collector.events():
        emit(
            watch.on_liquidation(event),
            dispatcher,
            send_event_alerts=send_event_alerts,
        )


async def poll_derivatives(
    client: BinanceDerivativesClient,
    watch: MarketWatch,
    dispatcher,
    *,
    interval_seconds: float,
    send_event_alerts: bool,
) -> None:
    while True:
        try:
            snapshot = await client.fetch_snapshot(watch.symbol)
            emit(
                watch.on_derivatives(snapshot),
                dispatcher,
                send_event_alerts=send_event_alerts,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            print(f"WATCH derivatives unavailable: {type(exc).__name__}: {exc}")
        await asyncio.sleep(interval_seconds)


async def periodic(
    watch: MarketWatch,
    dispatcher: TelegramDispatcher | None,
    *,
    interval_seconds: float,
    send_event_alerts: bool,
) -> None:
    while True:
        await asyncio.sleep(interval_seconds)
        emit(
            watch.tick(datetime.now(UTC)),
            dispatcher,
            send_event_alerts=send_event_alerts,
        )


async def evaluate_range_forecasts(
    tracker: RangeForwardTracker,
    *,
    interval_seconds: float = 60.0,
) -> None:
    while True:
        try:
            await tracker.evaluate_due()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            print(
                "RANGE_FORECAST evaluator unavailable: "
                f"{type(exc).__name__}: {exc}"
            )
        await asyncio.sleep(interval_seconds)


async def format_range_stats(
    repository: RangeForecastRepository,
    symbol: str | None,
) -> str:
    stats = await repository.coverage_stats(symbol=symbol)
    if not stats:
        target = symbol or "henüz hiçbir coin"
        return f"📊 {target} için forward sonuç yok."

    labels = {
        15: "15 dk",
        60: "1 saat",
        240: "4 saat",
        1440: "24 saat",
    }
    grouped: dict[str, list] = {}
    for item in stats:
        grouped.setdefault(item.symbol, []).append(item)

    lines = ["📊 Forward Range Stats", ""]
    for stat_symbol in sorted(grouped):
        lines.append(stat_symbol)
        for item in sorted(
            grouped[stat_symbol],
            key=lambda row: row.horizon_minutes,
        ):
            label = labels.get(
                item.horizon_minutes,
                f"{item.horizon_minutes} dk",
            )
            if item.evaluated:
                coverage = f"%{float(item.coverage_pct):.1f}"
                lines.append(
                    f"{label}: {item.evaluated} tahmin / {coverage} tuttu "
                    f"({item.covered}/{item.evaluated})"
                )
            else:
                lines.append(f"{label}: henüz değerlendirme yok")

            if item.pending or item.no_data:
                extra = []
                if item.pending:
                    extra.append(f"{item.pending} bekliyor")
                if item.no_data:
                    extra.append(f"{item.no_data} veri yok")
                lines.append("  " + ", ".join(extra))
        lines.append("")

    lines.append("Hedef coverage: %70")
    return "\n".join(lines).rstrip()


async def run(symbol: str, config: WatchConfig) -> None:
    settings = get_settings()
    dispatcher = None
    send_event_alerts = (
        os.getenv("WATCH_EVENT_ALERTS_ENABLED", "true").strip().lower()
        in {"1", "true", "yes", "on"}
    )
    database_engine = None
    range_tracker = None
    range_repository = None

    configured_database_url = os.getenv("DATABASE_URL", "").strip()
    if configured_database_url:
        try:
            database_engine = create_database_engine(settings.database_url)
            await initialize_database(database_engine)
            range_repository = RangeForecastRepository(
                create_session_factory(database_engine)
            )
            range_tracker = RangeForwardTracker(range_repository)
            print("RANGE_FORECAST database=enabled")
        except Exception as exc:  # noqa: BLE001
            print(
                "RANGE_FORECAST database unavailable; continuing without persistence: "
                f"{type(exc).__name__}: {exc}"
            )
            if database_engine is not None:
                await database_engine.dispose()
                database_engine = None
    else:
        print("RANGE_FORECAST database=disabled (DATABASE_URL not set)")

    if (
        settings.telegram_enabled
        and settings.telegram_bot_token
        and settings.telegram_chat_id
    ):
        dispatcher = TelegramDispatcher(
            TelegramNotifier(
                settings.telegram_bot_token,
                settings.telegram_chat_id,
            )
        )
        await dispatcher.start()
        print("WATCH telegram=enabled")
    else:
        print("WATCH telegram=disabled (alerts printed only)")

    watch = MarketWatch(symbol, config)
    print(
        "WATCH_START "
        f"symbol={watch.symbol} "
        f"whale_trade_usd={config.whale_trade_usd:,.0f} "
        f"window_min={int(config.window.total_seconds() // 60)} "
        f"price_move_pct={config.price_move_pct} "
        f"oi_move_pct={config.oi_move_pct} "
        f"flow_min_usd={config.flow_min_usd:,.0f} "
        f"liquidation_wave_usd={config.liquidation_wave_usd:,.0f} "
        f"levels={','.join(str(level) for level in config.levels) or 'none'} "
        f"event_alerts={'enabled' if send_event_alerts else 'disabled'}"
    )

    if dispatcher is not None:
        dispatcher.enqueue(
            f"👀 {watch.symbol} piyasa izleme başladı.\n"
            "Coin analizi için bota BTC, PYTH, PENDLE gibi coin adını yaz.\n"
            "Bu mesajlar tahmin değildir; piyasanın o anki durumunu anlatır."
        )

    symbols = [watch.symbol]
    command_poller = None
    if dispatcher is not None:
        command_poller = TelegramCommandPoller(
            bot_token=settings.telegram_bot_token,
            allowed_chat_id=settings.telegram_chat_id,
            analyzer=CoinAnalyzer(),
            on_analysis=(
                range_tracker.persist_analysis
                if range_tracker is not None
                else None
            ),
            stats_provider=(
                (
                    lambda requested_symbol: format_range_stats(
                        range_repository,
                        requested_symbol,
                    )
                )
                if range_repository is not None
                else None
            ),
            extra_allowed_chat_ids=tuple(
                item.strip()
                for item in os.getenv("TELEGRAM_ALLOWED_CHAT_IDS", "").split(",")
                if item.strip()
            ),
        )

    try:
        async with asyncio.TaskGroup() as group:
            group.create_task(
                consume_trades(
                    BinanceSpotTradeCollector(symbols),
                    watch,
                    dispatcher,
                    send_event_alerts=send_event_alerts,
                )
            )
            group.create_task(
                consume_trades(
                    BinanceFuturesTradeCollector(symbols),
                    watch,
                    dispatcher,
                    send_event_alerts=send_event_alerts,
                )
            )
            group.create_task(
                consume_liquidations(
                    BinanceLiquidationCollector(symbols),
                    watch,
                    dispatcher,
                    send_event_alerts=send_event_alerts,
                )
            )
            group.create_task(
                poll_derivatives(
                    BinanceDerivativesClient(),
                    watch,
                    dispatcher,
                    interval_seconds=60.0,
                    send_event_alerts=send_event_alerts,
                )
            )
            group.create_task(
                periodic(
                    watch,
                    dispatcher,
                    interval_seconds=60.0,
                    send_event_alerts=send_event_alerts,
                )
            )
            if command_poller is not None:
                group.create_task(command_poller.run())
            if range_tracker is not None:
                group.create_task(evaluate_range_forecasts(range_tracker))
    finally:
        if dispatcher is not None:
            await dispatcher.stop()
        if database_engine is not None:
            await database_engine.dispose()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--symbol", default=os.getenv("WATCH_SYMBOL", "BTCUSDT"))
    parser.add_argument(
        "--whale-usd",
        type=float,
        default=float(os.getenv("WATCH_WHALE_USD", "1000000")),
    )
    parser.add_argument(
        "--liquidation-usd",
        type=float,
        default=float(os.getenv("WATCH_LIQUIDATION_USD", "5000000")),
    )
    parser.add_argument(
        "--price-move-pct",
        type=float,
        default=float(os.getenv("WATCH_PRICE_MOVE_PCT", "0.5")),
    )
    parser.add_argument(
        "--oi-move-pct",
        type=float,
        default=float(os.getenv("WATCH_OI_MOVE_PCT", "1.0")),
    )
    parser.add_argument(
        "--flow-usd",
        type=float,
        default=float(os.getenv("WATCH_FLOW_USD", "5000000")),
    )
    parser.add_argument(
        "--levels",
        default=os.getenv("WATCH_LEVELS", ""),
        help="Comma-separated price levels to alert on, e.g. 82450,84199",
    )
    parser.add_argument(
        "--summary-minutes",
        type=int,
        default=int(os.getenv("WATCH_SUMMARY_MINUTES", "60")),
        help="Periodic market summary interval in minutes.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = WatchConfig(
        whale_trade_usd=args.whale_usd,
        liquidation_wave_usd=args.liquidation_usd,
        price_move_pct=args.price_move_pct,
        oi_move_pct=args.oi_move_pct,
        flow_min_usd=args.flow_usd,
        levels=tuple(
            float(item)
            for item in args.levels.split(",")
            if item.strip()
        ),
        summary_every=timedelta(minutes=args.summary_minutes),
    )
    asyncio.run(run(args.symbol, config))


if __name__ == "__main__":
    main()
