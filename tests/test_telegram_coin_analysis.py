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
                    volume=Decimal("10"),
                    quote_volume=Decimal("1000000"),
                    taker_buy_base_volume=Decimal("6"),
                    taker_buy_quote_volume=Decimal("600000"),
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
        flow_enabled=False,
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
        flow_enabled=False,
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
    assert TelegramCommandPoller._parse_stats_request("/stats@MyBot btc") == "BTCUSDT"
    assert TelegramCommandPoller._parse_stats_request("/stats@MyBot") is None
    assert TelegramCommandPoller._parse_stats_request("BTC") is False
    assert TelegramCommandPoller._parse_stats_request("/help") is False



def test_long_telegram_message_is_split_below_limit() -> None:
    text = "\n".join(f"line {index}: " + "x" * 100 for index in range(100))
    chunks = TelegramCommandPoller._split_message(text, limit=500)

    assert len(chunks) > 1
    assert all(len(chunk) <= 500 for chunk in chunks)
    assert "\n".join(chunks).replace("\n", "") == text.replace("\n", "")


def test_short_telegram_message_is_not_split() -> None:
    assert TelegramCommandPoller._split_message("hello", limit=500) == ["hello"]




@pytest.mark.asyncio
async def test_coin_analysis_renders_taker_flow_windows_and_price_zones() -> None:
    from crypto_signal_engine.watch.coin_analysis import CoinAnalyzer

    analyzer = CoinAnalyzer(
        candle_client=_FakeCandleClient(),
        derivatives_client=_FakeDerivativesClient(
            oi_5m="0.10",
            oi_15m="0.20",
            funding="0.0001",
            long_short="1.00",
        ),
        bybit_enabled=False,
        flow_enabled=True,
    )

    result = await analyzer.analyze("btc")

    assert "Agresif taker akışı (Binance Spot mum verisi)" in result.text
    assert "Money Flow ile aynı metrik değildir" in result.text
    for label in ("15dk", "30dk", "1s", "2s", "4s", "1g"):
        assert f"{label}:" in result.text

    assert (
        "15dk: net 30.00 BTC ($3.0M) · alım %60 · agresif alım baskısı"
    ) in result.text
    assert (
        "Detay: agresif alım 90.00 BTC ($9.0M) · "
        "agresif satım 60.00 BTC ($6.0M)"
    ) in result.text
    assert (
        "1s: net 120.00 BTC ($12.0M) · alım %60 · agresif alım baskısı"
    ) in result.text
    for removed in ("24s:", "12s:", "6s:", "5dk:", "3dk:", "1dk:"):
        assert f"\n{removed}" not in result.text
    assert "agresif alım baskısı" in result.text
    assert "Son 1s yoğun fiyat bölgeleri (1dk mum bazlı yaklaşık)" in result.text
    assert "Net alıcı bölge:" in result.text
    assert "Net satıcı bölge:" in result.text


def test_binance_kline_parser_captures_taker_buy_quote_volume() -> None:
    from crypto_signal_engine.collectors.binance.candles import BinanceSpotCandleClient

    row = [
        1_700_000_000_000,
        "100",
        "110",
        "90",
        "105",
        "12",
        1_700_000_059_999,
        "1250",
        42,
        "7",
        "760",
        "0",
    ]
    candle = BinanceSpotCandleClient._parse_row("BTCUSDT", "1m", row)

    assert candle.quote_volume == Decimal("1250")
    assert candle.taker_buy_base_volume == Decimal("7")
    assert candle.taker_buy_quote_volume == Decimal("760")
    assert candle.trade_count == 42



def test_flow_zones_rank_net_buyers_and_sellers_separately() -> None:
    from crypto_signal_engine.watch.coin_analysis import _flow_zones

    candles = [
        SimpleNamespace(
            high=Decimal("100"),
            low=Decimal("100"),
            close=Decimal("100"),
            quote_volume=Decimal("1000"),
            taker_buy_quote_volume=Decimal("900"),
        ),
        SimpleNamespace(
            high=Decimal("101"),
            low=Decimal("101"),
            close=Decimal("101"),
            quote_volume=Decimal("1000"),
            taker_buy_quote_volume=Decimal("100"),
        ),
    ]

    buyers, sellers = _flow_zones(
        candles,
        100.0,
        lookback_minutes=2,
        bucket_pct=0.5,
        top_n=3,
    )

    assert buyers and sellers
    assert buyers[0][2] == Decimal("800")
    assert sellers[0][2] == Decimal("800")
    assert buyers[0][:2] != sellers[0][:2]



def test_detail_flag_is_split_from_request() -> None:
    split = TelegramCommandPoller._split_detail_flag
    assert split("BTC") == ("BTC", False)
    assert split("BTC detay") == ("BTC", True)
    assert split("/analyze eth DETAY") == ("/analyze eth", True)
    assert split("detay") == ("detay", False)
    command, detailed = split("pendle uzun")
    assert detailed
    assert TelegramCommandPoller._parse_request(command) == "pendle"


@pytest.mark.asyncio
async def test_coin_analysis_has_short_mobile_summary() -> None:
    from crypto_signal_engine.watch.coin_analysis import CoinAnalyzer

    analyzer = CoinAnalyzer(
        candle_client=_FakeCandleClient(),
        derivatives_client=_FakeDerivativesClient(
            oi_5m="0.10",
            oi_15m="0.20",
            funding="0.0001",
            long_short="1.00",
        ),
        bybit_enabled=False,
        flow_enabled=True,
    )

    result = await analyzer.analyze("btc")
    summary = result.summary

    assert summary.startswith("🔎 BTCUSDT — ")
    assert len(summary.splitlines()) <= 11
    assert len(summary) < len(result.text) / 2
    assert "Aralık (%70 hedef):" in summary
    assert "Taker akışı (net): 15dk +30.00 BTC" in summary
    assert "Alım payı: 15dk %60" in summary
    assert "Vadeli: OI 15dk +0.20% · Funding 0.0100% · L/S 1.00" in summary
    assert "Uzun rapor: BTC detay" in summary
    assert "Agresif taker akışı" not in summary


class _FakeAnalyzer:
    def __init__(self) -> None:
        self.requests: list[str] = []

    async def analyze(self, request: str):
        from crypto_signal_engine.watch.coin_analysis import CoinAnalysis

        self.requests.append(request)
        return CoinAnalysis(
            symbol="BTCUSDT",
            generated_at=T0,
            text="UZUN RAPOR",
            summary="KISA\nalt satır",
        )


@pytest.mark.asyncio
async def test_poller_sends_summary_by_default_and_full_on_detay() -> None:
    analyzer = _FakeAnalyzer()
    sent: list[str] = []
    persisted: list[str] = []

    async def line(symbol: str) -> str:
        return "KARNE SATIRI"

    async def full(symbol: str) -> str:
        return "KARNE UZUN"

    async def persist(analysis) -> None:
        persisted.append(analysis.symbol)

    poller = TelegramCommandPoller(
        bot_token="token",
        allowed_chat_id="1",
        analyzer=analyzer,
        on_analysis=persist,
        scorecard_provider=full,
        scorecard_line_provider=line,
    )

    async def fake_send(chat_id: str, text: str) -> None:
        sent.append(text)

    poller._send = fake_send  # type: ignore[method-assign]

    def update(text: str) -> dict:
        return {"message": {"chat": {"id": 1}, "from": {}, "text": text}}

    await poller._handle(update("BTC"))
    await poller._handle(update("BTC detay"))

    assert analyzer.requests == ["BTC", "BTC"]
    assert persisted == ["BTCUSDT", "BTCUSDT"]
    assert sent[0] == "KISA\nKARNE SATIRI\nalt satır"
    assert sent[1] == "UZUN RAPOR\n\nKARNE UZUN"


@pytest.mark.asyncio
async def test_analyze_command_keeps_detailed_analysis_path() -> None:
    analyzer = _FakeAnalyzer()
    sent: list[str] = []

    poller = TelegramCommandPoller(
        bot_token="token",
        allowed_chat_id="111",
        analyzer=analyzer,
    )

    async def fake_send(chat_id: str, text: str) -> None:
        sent.append(text)

    poller._send = fake_send  # type: ignore[method-assign]
    await poller._handle(
        {"message": {"chat": {"id": 111}, "from": {}, "text": "/analyze BTC"}}
    )

    assert sent == ["UZUN RAPOR"]
    assert analyzer.requests == ["BTC"]
