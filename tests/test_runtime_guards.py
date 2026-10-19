from crypto_signal_engine.collectors.binance.candles import BinanceSpotCandleClient
from crypto_signal_engine.examples import market_watch


def test_binance_kline_limit_never_exceeds_exchange_maximum() -> None:
    assert BinanceSpotCandleClient._api_limit(100) == 101
    assert BinanceSpotCandleClient._api_limit(999) == 1000
    assert BinanceSpotCandleClient._api_limit(1000) == 1000
    assert BinanceSpotCandleClient._api_limit(5000) == 1000


def test_local_database_detection() -> None:
    assert market_watch._is_local_database_url(
        "postgresql+asyncpg://user:secret@localhost:5432/crypto"
    )
    assert market_watch._is_local_database_url(
        "postgresql://user:secret@127.0.0.1:5432/crypto"
    )
    assert not market_watch._is_local_database_url(
        "postgresql://user:secret@postgres.railway.internal:5432/railway"
    )


def test_database_target_hides_credentials() -> None:
    target = market_watch._database_target(
        "postgresql://alice:supersecret@postgres.railway.internal:5432/railway"
    )
    assert target == "postgres.railway.internal:5432/railway"
    assert "alice" not in target
    assert "supersecret" not in target


def test_running_on_railway_uses_railway_environment(monkeypatch) -> None:
    monkeypatch.delenv("RAILWAY_ENVIRONMENT", raising=False)
    monkeypatch.delenv("RAILWAY_PROJECT_ID", raising=False)
    monkeypatch.delenv("RAILWAY_SERVICE_ID", raising=False)
    assert market_watch._running_on_railway() is False

    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
    assert market_watch._running_on_railway() is True


def test_railway_postgres_url_is_converted_for_async_engine() -> None:
    from crypto_signal_engine.db.session import create_database_engine

    railway_url = "postgresql://postgres:pw@postgres.railway.internal:5432/railway"
    converted = market_watch._async_database_url(railway_url)
    assert converted.startswith("postgresql+asyncpg://")
    # Must not raise (a raw postgresql:// URL needs psycopg and fails).
    create_database_engine(converted)
    assert market_watch._async_database_url("postgres://a:b@h:5432/d").startswith(
        "postgresql+asyncpg://"
    )
