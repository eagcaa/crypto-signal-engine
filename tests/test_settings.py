from crypto_signal_engine.config.settings import Settings


def test_railway_postgres_url_is_normalized_for_asyncpg() -> None:
    settings = Settings(
        _env_file=None,
        database_url="postgresql://user:pass@host:5432/db",
    )
    assert settings.database_url == "postgresql+asyncpg://user:pass@host:5432/db"


def test_legacy_postgres_url_is_normalized_for_asyncpg() -> None:
    settings = Settings(
        _env_file=None,
        database_url="postgres://user:pass@host:5432/db",
    )
    assert settings.database_url == "postgresql+asyncpg://user:pass@host:5432/db"


def test_asyncpg_url_is_left_unchanged() -> None:
    url = "postgresql+asyncpg://user:pass@host:5432/db"
    settings = Settings(_env_file=None, database_url=url)
    assert settings.database_url == url
