import pytest

from crypto_signal_engine.config.settings import Settings
from crypto_signal_engine.examples.railway_candidate import (
    validate_railway_candidate_settings,
)


def _settings(**overrides) -> Settings:
    values = {
        "database_url": "postgresql+asyncpg://user:pass@db.example:5432/crypto",
        "paper_trading_enabled": False,
        "candidate_paper_enabled": True,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_railway_candidate_accepts_safe_mode() -> None:
    validate_railway_candidate_settings(_settings())


def test_railway_candidate_rejects_general_paper_mode() -> None:
    with pytest.raises(RuntimeError, match="PAPER_TRADING_ENABLED=false"):
        validate_railway_candidate_settings(
            _settings(paper_trading_enabled=True)
        )


def test_railway_candidate_requires_candidate_mode() -> None:
    with pytest.raises(RuntimeError, match="CANDIDATE_PAPER_ENABLED=true"):
        validate_railway_candidate_settings(
            _settings(candidate_paper_enabled=False)
        )


def test_railway_candidate_rejects_local_database() -> None:
    with pytest.raises(RuntimeError, match="remote DATABASE_URL"):
        validate_railway_candidate_settings(
            _settings(
                database_url="postgresql+asyncpg://crypto:crypto@localhost:5432/crypto"
            )
        )


def test_railway_candidate_requires_telegram_credentials_when_enabled() -> None:
    with pytest.raises(RuntimeError, match="TELEGRAM_BOT_TOKEN"):
        validate_railway_candidate_settings(
            _settings(
                telegram_enabled=True,
                telegram_bot_token="",
                telegram_chat_id="",
            )
        )
