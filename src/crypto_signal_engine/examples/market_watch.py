"""Live market-watch alerts (context only, no predictions, no orders).

Streams Binance spot/futures trades and liquidations, polls Binance
derivatives once a minute, and prints/sends short Turkish alerts.
Runs independently of the research collector and needs no database.
"""

import argparse
import asyncio
import os
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

from crypto_signal_engine.collectors.binance import (
    BinanceDerivativesClient,
    BinanceFuturesTradeCollector,
    BinanceLiquidationCollector,
    BinanceSpotTradeCollector,
)
from crypto_signal_engine.config.settings import Settings, get_settings
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


_LOCAL_DATABASE_HOSTS = {"localhost", "127.0.0.1", "::1", "host.docker.internal"}


def _database_target(database_url: str) -> str:
    """Return a credential-free database target for startup logs."""
    try:
        parsed = urlparse(database_url)
        host = parsed.hostname or "unknown"
        port = parsed.port or 5432
        database = (parsed.path or "").lstrip("/") or "unknown"
        return f"{host}:{port}/{database}"
    except (TypeError, ValueError):
        return "unknown"


def _is_local_database_url(database_url: str) -> bool:
    try:
        host = (urlparse(database_url).hostname or "").lower()
    except (TypeError, ValueError):
        return False
    return host in _LOCAL_DATABASE_HOSTS


def _async_database_url(database_url: str) -> str:
    """Railway gives postgresql://...; the async engine needs +asyncpg."""
    return str(Settings.normalize_database_url(database_url))


def _running_on_railway() -> bool:
    return bool(
        os.getenv("RAILWAY_ENVIRONMENT")
        or os.getenv("RAILWAY_PROJECT_ID")
        or os.getenv("RAILWAY_SERVICE_ID")
    )


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


def _calibration_label(sample_count: int) -> str:
    if sample_count < 20:
        return "Az veri"
    if sample_count < 50:
        return "Orta veri"
    return "Güçlü veri"


def _scorecard_score(item) -> int:
    if not item.independent:
        return 0

    coverage = item.independent_covered * 100.0 / item.independent
    coverage_fit = max(0.0, 100.0 - abs(coverage - 70.0) * 3.0)
    sample_score = min(item.independent / 50.0, 1.0) * 100.0

    if item.avg_band_radius_pct and item.avg_band_radius_pct > 0:
        overshoot_ratio = (item.avg_overshoot_pct or 0.0) / item.avg_band_radius_pct
        overshoot_score = max(0.0, 100.0 - overshoot_ratio * 100.0)
    else:
        overshoot_score = 100.0

    score = round(
        coverage_fit * 0.45
        + sample_score * 0.40
        + overshoot_score * 0.15
    )

    # A tiny sample must never look as authoritative as a mature track record.
    if item.independent < 10:
        score = min(score, 59)
    elif item.independent < 20:
        score = min(score, 69)
    elif item.independent < 50:
        score = min(score, 84)
    return max(0, min(100, score))


def _score_label(score: int, sample_count: int) -> str:
    if sample_count < 20:
        return "Ön değerlendirme"
    if score >= 85:
        return "Çok iyi"
    if score >= 70:
        return "İyi"
    if score >= 55:
        return "İzle"
    return "Zayıf"


def _scorecard_lines(item, label: str) -> list[str]:
    if not item.independent:
        return [
            f"📊 {label} Tahmin Karnesi",
            "Henüz değerlendirilmiş bağımsız tahmin yok.",
        ]

    coverage = item.independent_covered * 100.0 / item.independent
    score = _scorecard_score(item)
    lines = [
        f"📊 {label} Tahmin Karnesi",
        f"{score}/100 — {_score_label(score, item.independent)}",
        "",
        "Geçmiş performans",
        f"• {item.independent} bağımsız tahmin",
        (
            f"• {item.independent_covered} tanesi bant içinde kaldı "
            f"(%{coverage:.0f})"
        ),
    ]
    if item.avg_band_radius_pct is not None:
        lines.append(f"• Ortalama bant: ±%{item.avg_band_radius_pct:.2f}")
    if item.avg_overshoot_pct is not None:
        lines.append(
            "• Bant dışına çıkanlarda ortalama taşma: "
            f"%{item.avg_overshoot_pct:.2f}"
        )
    if item.max_overshoot_pct is not None:
        lines.append(f"• En kötü taşma: %{item.max_overshoot_pct:.2f}")

    lines.extend(
        [
            "",
            f"Kalibrasyon: {_calibration_label(item.independent)}",
            (
                f"Taşma yönü: ↑ {item.upward_breaches} · "
                f"↓ {item.downward_breaches}"
            ),
        ]
    )

    if item.independent < 20:
        comment = "Henüz erken; örnek sayısı arttıkça karne daha anlamlı olacak."
    elif coverage < 65:
        comment = "Bantlar hedeflenen %70 kapsama göre dar kalıyor."
    elif coverage > 78:
        comment = "Bantlar hedeflenen %70 kapsama göre geniş olabilir."
    else:
        comment = "Bantlar hedeflenen %70 kapsama oranına yakın çalışıyor."
    lines.extend(["", f"Yorum: {comment}"])
    return lines


async def format_range_scorecard(
    repository: RangeForecastRepository,
    symbol: str,
) -> str | None:
    stats = await repository.coverage_stats(symbol=symbol)
    evaluated = [item for item in stats if item.independent > 0]
    if not evaluated:
        return None

    preferred = next(
        (item for item in evaluated if item.horizon_minutes == 60),
        evaluated[0],
    )
    label = {
        15: "15 Dakikalık",
        60: "1 Saatlik",
        240: "4 Saatlik",
        1440: "24 Saatlik",
    }.get(preferred.horizon_minutes, f"{preferred.horizon_minutes} Dakikalık")
    lines = _scorecard_lines(preferred, f"{preferred.symbol} · {label}")
    lines.extend(
        [
            "",
            f"Tüm ufuklar için: /stats {preferred.symbol.removesuffix('USDT')}",
            "ℹ️ Geçmiş performans gelecekteki sonucu garanti etmez.",
        ]
    )
    return "\n".join(lines)


async def format_range_stats(
    repository: RangeForecastRepository,
    symbol: str | None,
) -> str:
    stats = await repository.coverage_stats(symbol=symbol)
    if not stats:
        target = symbol or "henüz hiçbir coin"
        return f"📊 {target} için forward sonuç yok."

    labels = {
        15: "15 Dakikalık",
        60: "1 Saatlik",
        240: "4 Saatlik",
        1440: "24 Saatlik",
    }
    grouped: dict[str, list] = {}
    for item in stats:
        grouped.setdefault(item.symbol, []).append(item)

    lines: list[str] = []
    for stat_symbol in sorted(grouped):
        if lines:
            lines.extend(["", "────────────", ""])
        symbol_rows = sorted(
            grouped[stat_symbol],
            key=lambda row: row.horizon_minutes,
        )
        for index, item in enumerate(symbol_rows):
            if index:
                lines.append("")
            label = labels.get(
                item.horizon_minutes,
                f"{item.horizon_minutes} Dakikalık",
            )
            lines.extend(_scorecard_lines(item, f"{stat_symbol} · {label}"))
            if item.pending or item.no_data:
                status = []
                if item.pending:
                    status.append(f"{item.pending} bekliyor")
                if item.no_data:
                    status.append(f"{item.no_data} veri yok")
                lines.append("Durum: " + ", ".join(status))

    lines.extend(
        [
            "",
            "Karne skoru yalnız isabet yüzdesi değildir; hedef %70 kapsama, "
            "bağımsız örnek sayısı ve taşma büyüklüğü birlikte değerlendirilir.",
            "ℹ️ Geçmiş performans gelecekteki sonucu garanti etmez.",
        ]
    )
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
        if _running_on_railway() and _is_local_database_url(configured_database_url):
            print(
                "RANGE_FORECAST database=disabled "
                "reason=localhost_database_url "
                f"target={_database_target(configured_database_url)}"
            )
        else:
            try:
                database_engine = create_database_engine(
                    _async_database_url(configured_database_url)
                )
                await initialize_database(database_engine)
                range_repository = RangeForecastRepository(
                    create_session_factory(database_engine)
                )
                range_tracker = RangeForwardTracker(range_repository)
                print(
                    "RANGE_FORECAST database=enabled "
                    f"target={_database_target(configured_database_url)}"
                )
            except Exception as exc:  # noqa: BLE001
                print(
                    "RANGE_FORECAST database unavailable; "
                    "continuing without persistence: "
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
                dict.fromkeys(
                    (
                        "1057931349",
                        *(
                            item.strip()
                            for item in os.getenv(
                                "TELEGRAM_ALLOWED_CHAT_IDS",
                                "",
                            ).split(",")
                            if item.strip()
                        ),
                    )
                )
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
