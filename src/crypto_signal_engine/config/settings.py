from decimal import Decimal
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    log_level: str = "INFO"

    database_url: str = (
        "postgresql+asyncpg://crypto:crypto@localhost:5432/crypto_signal_engine"
    )

    binance_enabled: bool = True
    bybit_enabled: bool = True
    coinglass_enabled: bool = False

    paper_trading_enabled: bool = False
    paper_starting_equity: Decimal = Decimal("10000")
    paper_risk_per_trade_pct: Decimal = Decimal("0.50")
    paper_max_notional_pct: Decimal = Decimal("10")
    paper_max_open_positions: int = 1
    paper_max_drawdown_pct: Decimal = Decimal("5.00")
    paper_max_consecutive_losses: int = 5

    telegram_enabled: bool = False
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    calibration_file: str = "runtime-data/calibration.json"

    binance_api_key: str = ""
    binance_api_secret: str = ""
    bybit_api_key: str = ""
    bybit_api_secret: str = ""
    coinglass_api_key: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
