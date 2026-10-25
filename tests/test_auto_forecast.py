import asyncio
from types import SimpleNamespace

from crypto_signal_engine.examples import market_watch


def test_auto_forecast_settings(monkeypatch) -> None:
    monkeypatch.delenv("AUTO_FORECAST_SYMBOLS", raising=False)
    monkeypatch.delenv("AUTO_FORECAST_MINUTES", raising=False)
    symbols, interval = market_watch.auto_forecast_settings()
    assert symbols == market_watch.DEFAULT_AUTO_FORECAST_SYMBOLS
    assert interval == 3600

    monkeypatch.setenv("AUTO_FORECAST_SYMBOLS", "btc, sol ,BTCUSDT")
    monkeypatch.setenv("AUTO_FORECAST_MINUTES", "30")
    symbols, interval = market_watch.auto_forecast_settings()
    assert symbols == ("BTCUSDT", "SOLUSDT")
    assert interval == 1800

    monkeypatch.setenv("AUTO_FORECAST_SYMBOLS", "off")
    assert market_watch.auto_forecast_settings()[0] == ()

    monkeypatch.setenv("AUTO_FORECAST_MINUTES", "1")
    assert market_watch.auto_forecast_settings()[1] == 300


def test_auto_forecast_saves_each_symbol_and_skips_failures() -> None:
    saved: list[str] = []

    class Analyzer:
        async def analyze(self, symbol):
            if symbol == "BADUSDT":
                raise RuntimeError("not listed")
            return SimpleNamespace(symbol=symbol)

    class Tracker:
        async def persist_analysis(self, analysis):
            saved.append(analysis.symbol)

    count = asyncio.run(
        market_watch.auto_forecast_once(
            Analyzer(),
            Tracker(),
            ("BTCUSDT", "BADUSDT", "ETHUSDT"),
            pause_seconds=0,
        )
    )
    assert count == 2
    assert saved == ["BTCUSDT", "ETHUSDT"]
