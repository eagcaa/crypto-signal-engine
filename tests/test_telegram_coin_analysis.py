from datetime import UTC, datetime, timedelta
from decimal import Decimal
from math import exp
from types import SimpleNamespace

import pytest

from crypto_signal_engine.forecast.range import PricePoint, estimate_live_range
from crypto_signal_engine.integrations.telegram_commands import TelegramCommandPoller
from crypto_signal_engine.watch.coin_analysis import normalize_symbol


T0 = datetime(2026, 1, 1, tzinfo=UTC)


def test_normalize_symbol_accepts_plain_and_usdt() -> None:
    assert normalize_symbol("btc") == "BTCUSDT"
    assert normalize_symbol("PYTHUSDT") == "PYTHUSDT"
    assert normalize_symbol("/analyze pendle") == "PENDLEUSDT"
    assert normalize_symbol("/eth@MyBot") == "ETHUSDT"


def test_command_parser_accepts_plain_ticker() -> None:
    assert TelegramCommandPoller._parse_request("BTC") == "BTC"
    assert TelegramCommandPoller._parse_request("/analyze PYTH") == "PYTH"
    assert TelegramCommandPoller._parse_request("/start") is None
    assert TelegramCommandPoller._parse_request("btc usdt") == "btcusdt"
    assert TelegramCommandPoller._parse_request("/analyze") is None
    assert TelegramCommandPoller._parse_request("/pendle@MyBot") == "pendle"


def test_command_poller_accepts_extra_allowed_chats() -> None:
    poller = TelegramCommandPoller(
        bot_token="token",
        allowed_chat_id="111",
        analyzer=None,
        extra_allowed_chat_ids=("222", " "),
    )
    assert poller._allowed_chat_ids == {"111", "222"}


def test_live_range_uses_only_completed_history() -> None:
    points = [
        PricePoint(
            timestamp=T0 + timedelta(minutes=index),
            price=100.0 * exp(index * 0.0001),
        )
        for index in range(60 * 24 + 1)
    ]
    estimate = estimate_live_range(
        points,
        horizon_minutes=15,
        target_coverage=0.70,
        minimum_calibration_count=20,
        volatility_scaled=False,
    )
    assert estimate is not None
    assert estimate.lower < points[-1].price < estimate.upper
    assert estimate.calibration_count >= 20



def test_coin_analyzer_can_disable_bybit() -> None:
    from crypto_signal_engine.watch.coin_analysis import CoinAnalyzer

    analyzer = CoinAnalyzer(bybit_enabled=False)
    assert analyzer._bybit_enabled is False


def test_coin_analyzer_can_enable_bybit() -> None:
    from crypto_signal_engine.watch.coin_analysis import CoinAnalyzer

    analyzer = CoinAnalyzer(bybit_enabled=True)
    assert analyzer._bybit_enabled is True



class _FakeCandleClient:
    async def fetch_closed(
        self,
        symbol: str,
        *,
        interval: str,
        limit: int = 100,
        start_time=None,
    ):
        step_minutes = {
            "1m": 1,
            "5m": 5,
            "15m": 15,
            "1h": 60,
        }[interval]
        count = min(limit, 1000)
        start = T0
        candles = []
        for index in range(count):
            price = 100.0 * exp(index * 0.00005)
            close_time = start + timedelta(minutes=step_minutes * index)
            candles.append(
                SimpleNamespace(
                    close_time=close_time,
                    close=Decimal(str(price)),
                    high=Decimal(str(price * 1.001)),
                    low=Decimal(str(price * 0.999)),
                )
            )
        return candles


class _FakeDerivativesClient:
    def __init__(
        self,
        *,
        oi_5m: str,
        oi_15m: str,
        funding: str,
        long_short: str,
        fail: bool = False,
    ) -> None:
        self._snapshot = SimpleNamespace(
            oi_change_5m_pct=Decimal(oi_5m),
            oi_change_15m_pct=Decimal(oi_15m),
            funding_rate=Decimal(funding),
            long_short_ratio=Decimal(long_short),
        )
        self._fail = fail

    async def fetch_snapshot(self, symbol: str):
        if self._fail:
            raise RuntimeError("unavailable")
        return self._snapshot


@pytest.mark.asyncio
async def test_coin_analysis_renders_binance_and_bybit_derivatives() -> None:
    from crypto_signal_engine.watch.coin_analysis import CoinAnalyzer

    analyzer = CoinAnalyzer(
        candle_client=_FakeCandleClient(),
        derivatives_client=_FakeDerivativesClient(
            oi_5m="1.25",
            oi_15m="2.50",
            funding="0.0001",
            long_short="1.10",
        ),
        bybit_derivatives_client=_FakeDerivativesClient(
            oi_5m="-0.75",
            oi_15m="0.40",
            funding="-0.0002",
            long_short="0.90",
        ),
        bybit_enabled=True,
    )

    result = await analyzer.analyze("btc")

    # Every saved band is centred on the freshest (1m) price and starts now.
    assert result.forecasts
    minute_candles = await _FakeCandleClient().fetch_closed(
        "BTCUSDT",
        interval="1m",
        limit=1000,
    )
    current = float(minute_candles[-1].close)
    for forecast in result.forecasts:
        assert forecast.anchor_price == current
        assert forecast.anchor_at == result.generated_at
        assert forecast.lower_price < current < forecast.upper_price

    assert "Binance:" in result.text
    assert "OI 5 dk: +1.25%" in result.text
    assert "OI 15 dk: +2.50%" in result.text
    assert "Funding: 0.0100%" in result.text
    assert "Long/short: 1.10" in result.text

    assert "Bybit:" in result.text
    assert "OI 5 dk: -0.75%" in result.text
    assert "OI 15 dk: +0.40%" in result.text
    assert "Funding: -0.0200%" in result.text
    assert "Long/short: 0.90" in result.text


@pytest.mark.asyncio
async def test_coin_analysis_survives_bybit_unavailable() -> None:
    from crypto_signal_engine.watch.coin_analysis import CoinAnalyzer

    analyzer = CoinAnalyzer(
        candle_client=_FakeCandleClient(),
        derivatives_client=_FakeDerivativesClient(
            oi_5m="0.10",
            oi_15m="0.20",
            funding="0.0001",
            long_short="1.00",
        ),
        bybit_derivatives_client=_FakeDerivativesClient(
            oi_5m="0",
            oi_15m="0",
            funding="0",
            long_short="1",
            fail=True,
        ),
        bybit_enabled=True,
    )

    result = await analyzer.analyze("eth")

    assert "Binance:" in result.text
    assert "Bybit: veri alınamadı / mevcut değil" in result.text



def test_live_range_with_wicks_is_wider_than_close_only() -> None:
    import random

    rng = random.Random(4)
    points = []
    extremes = []
    price = 100.0
    for index in range(60 * 24 + 1):
        price *= exp(rng.gauss(0.0, 0.0005))
        points.append(
            PricePoint(
                timestamp=T0 + timedelta(minutes=index),
                price=price,
            )
        )
        wick = abs(rng.gauss(0.0, 0.0008))
        extremes.append((price * exp(-wick), price * exp(wick)))

    closes_only = estimate_live_range(points, horizon_minutes=15)
    with_wicks = estimate_live_range(
        points,
        horizon_minutes=15,
        extremes=extremes,
    )
    assert closes_only is not None and with_wicks is not None
    assert (
        with_wicks.upper - with_wicks.lower
        > closes_only.upper - closes_only.lower
    )



def test_stats_command_parser() -> None:
    assert TelegramCommandPoller._parse_stats_request("/stats") is None
    assert TelegramCommandPoller._parse_stats_request("/stats BTC") == "BTCUSDT"
    assert (
        TelegramCommandPoller._parse_stats_request("/stats ethusdt")
        == "ETHUSDT"
    )
    assert TelegramCommandPoller._parse_stats_request("BTC") is False
    assert TelegramCommandPoller._parse_stats_request("/help") is False
