"""Live market-watch alerts (context only, no predictions, no orders).

Streams Binance spot/futures trades and liquidations, polls Binance
derivatives once a minute, and prints/sends short Turkish alerts.
Runs independently of the research collector and needs no database.
"""

import argparse
import asyncio
from datetime import UTC, datetime

from crypto_signal_engine.collectors.binance import (
    BinanceDerivativesClient,
    BinanceFuturesTradeCollector,
    BinanceLiquidationCollector,
    BinanceSpotTradeCollector,
)
from crypto_signal_engine.config.settings import get_settings
from crypto_signal_engine.integrations import (
    TelegramDispatcher,
    TelegramNotifier,
)
from crypto_signal_engine.watch import MarketWatch, WatchAlert, WatchConfig


def emit(
    alerts: list[WatchAlert],
    dispatcher: TelegramDispatcher | None,
) -> None:
    for alert in alerts:
        print(
            f"WATCH_ALERT kind={alert.kind} "
            f"time={alert.timestamp.isoformat()}\n{alert.text}\n"
        )
        if dispatcher is not None and not dispatcher.enqueue(alert.text):
            print("TELEGRAM queue unavailable/full; alert dropped")


async def consume_trades(collector, watch, dispatcher) -> None:
    async for trade in collector.trades():
        emit(watch.on_trade(trade), dispatcher)


async def consume_liquidations(collector, watch, dispatcher) -> None:
    async for event in collector.events():
        emit(watch.on_liquidation(event), dispatcher)


async def poll_derivatives(
    client: BinanceDerivativesClient,
    watch: MarketWatch,
    dispatcher,
    *,
    interval_seconds: float,
) -> None:
    while True:
        try:
            snapshot = await client.fetch_snapshot(watch.symbol)
            emit(watch.on_derivatives(snapshot), dispatcher)
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
) -> None:
    while True:
        await asyncio.sleep(interval_seconds)
        emit(watch.tick(datetime.now(UTC)), dispatcher)


async def run(symbol: str, config: WatchConfig) -> None:
    settings = get_settings()
    dispatcher = None

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
        f"levels={','.join(str(level) for level in config.levels) or 'none'}"
    )

    if dispatcher is not None:
        dispatcher.enqueue(
            f"👀 {watch.symbol} piyasa izleme başladı.\n"
            "Bu mesajlar tahmin değildir; piyasanın o anki durumunu anlatır."
        )

    symbols = [watch.symbol]
    try:
        async with asyncio.TaskGroup() as group:
            group.create_task(
                consume_trades(
                    BinanceSpotTradeCollector(symbols),
                    watch,
                    dispatcher,
                )
            )
            group.create_task(
                consume_trades(
                    BinanceFuturesTradeCollector(symbols),
                    watch,
                    dispatcher,
                )
            )
            group.create_task(
                consume_liquidations(
                    BinanceLiquidationCollector(symbols),
                    watch,
                    dispatcher,
                )
            )
            group.create_task(
                poll_derivatives(
                    BinanceDerivativesClient(),
                    watch,
                    dispatcher,
                    interval_seconds=60.0,
                )
            )
            group.create_task(
                periodic(
                    watch,
                    dispatcher,
                    interval_seconds=60.0,
                )
            )
    finally:
        if dispatcher is not None:
            await dispatcher.stop()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--whale-usd", type=float, default=1_000_000.0)
    parser.add_argument("--liquidation-usd", type=float, default=5_000_000.0)
    parser.add_argument("--price-move-pct", type=float, default=0.5)
    parser.add_argument("--oi-move-pct", type=float, default=1.0)
    parser.add_argument("--flow-usd", type=float, default=5_000_000.0)
    parser.add_argument(
        "--levels",
        default="",
        help="Comma-separated price levels to alert on, e.g. 82450,84199",
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
    )
    asyncio.run(run(args.symbol, config))


if __name__ == "__main__":
    main()
