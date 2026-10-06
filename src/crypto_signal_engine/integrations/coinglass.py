import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import aiohttp


class CoinGlassApiError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class CoinGlassMarketSnapshot:
    symbol: str
    timestamp: datetime
    open_interest_usd: Decimal | None
    oi_change_5m_pct: Decimal | None
    oi_change_15m_pct: Decimal | None
    funding_rate_binance: Decimal | None
    funding_rate_bybit: Decimal | None
    taker_buy_ratio: Decimal | None
    taker_sell_ratio: Decimal | None
    taker_buy_volume_usd: Decimal | None
    taker_sell_volume_usd: Decimal | None


class CoinGlassClient:
    BASE_URL = "https://open-api-v4.coinglass.com"

    def __init__(
        self,
        api_key: str,
        *,
        timeout_seconds: float = 10.0,
    ) -> None:
        if not api_key.strip():
            raise ValueError("CoinGlass API key is required.")

        self._api_key = api_key.strip()
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)

    async def fetch_market_snapshot(
        self,
        *,
        symbol: str,
    ) -> CoinGlassMarketSnapshot:
        base_asset = self._base_asset(symbol)

        oi_task = self._get(
            "/api/futures/open-interest/exchange-list",
            params={"symbol": base_asset},
        )
        funding_task = self._get(
            "/api/futures/funding-rate/exchange-list",
        )
        taker_task = self._get(
            "/api/futures/taker-buy-sell-volume/exchange-list",
            params={"symbol": base_asset, "range": "5m"},
        )

        oi_payload, funding_payload, taker_payload = await asyncio.gather(
            oi_task,
            funding_task,
            taker_task,
        )

        oi_all = self._find_exchange(
            self._require_list(oi_payload.get("data"), "open interest data"),
            "All",
        )
        funding_coin = self._find_symbol(
            self._require_list(funding_payload.get("data"), "funding data"),
            base_asset,
        )

        funding_binance = None
        funding_bybit = None
        if funding_coin is not None:
            stablecoin_margin = funding_coin.get("stablecoin_margin_list") or []
            funding_binance = self._funding_for_exchange(
                stablecoin_margin,
                "Binance",
            )
            funding_bybit = self._funding_for_exchange(
                stablecoin_margin,
                "Bybit",
            )

        taker_data = taker_payload.get("data")
        if not isinstance(taker_data, dict):
            taker_data = {}

        return CoinGlassMarketSnapshot(
            symbol=symbol.upper(),
            timestamp=datetime.now(UTC),
            open_interest_usd=self._decimal_or_none(
                oi_all.get("open_interest_usd") if oi_all else None
            ),
            oi_change_5m_pct=self._decimal_or_none(
                oi_all.get("open_interest_change_percent_5m") if oi_all else None
            ),
            oi_change_15m_pct=self._decimal_or_none(
                oi_all.get("open_interest_change_percent_15m") if oi_all else None
            ),
            funding_rate_binance=funding_binance,
            funding_rate_bybit=funding_bybit,
            taker_buy_ratio=self._decimal_or_none(taker_data.get("buy_ratio")),
            taker_sell_ratio=self._decimal_or_none(taker_data.get("sell_ratio")),
            taker_buy_volume_usd=self._decimal_or_none(
                taker_data.get("buy_vol_usd")
            ),
            taker_sell_volume_usd=self._decimal_or_none(
                taker_data.get("sell_vol_usd")
            ),
        )

    async def _get(
        self,
        path: str,
        *,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        headers = {
            "accept": "application/json",
            "CG-API-KEY": self._api_key,
        }

        async with aiohttp.ClientSession(
            timeout=self._timeout,
            headers=headers,
        ) as session:
            async with session.get(
                f"{self.BASE_URL}{path}",
                params=params,
            ) as response:
                payload = await response.json(content_type=None)

                if response.status >= 400:
                    raise CoinGlassApiError(
                        f"CoinGlass HTTP {response.status}: {payload}"
                    )

                if not isinstance(payload, dict):
                    raise CoinGlassApiError(
                        "CoinGlass returned a non-object response."
                    )

                if str(payload.get("code")) != "0":
                    raise CoinGlassApiError(
                        f"CoinGlass API error: {payload}"
                    )

                return payload

    @staticmethod
    def _base_asset(symbol: str) -> str:
        normalized = symbol.upper()
        for quote in ("USDT", "USDC", "USD"):
            if normalized.endswith(quote):
                return normalized[: -len(quote)]
        return normalized

    @staticmethod
    def _require_list(value: Any, name: str) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            raise CoinGlassApiError(f"CoinGlass {name} is not a list.")
        return [item for item in value if isinstance(item, dict)]

    @staticmethod
    def _find_exchange(
        rows: list[dict[str, Any]],
        exchange: str,
    ) -> dict[str, Any] | None:
        target = exchange.casefold()
        return next(
            (
                row
                for row in rows
                if str(row.get("exchange", "")).casefold() == target
            ),
            None,
        )

    @staticmethod
    def _find_symbol(
        rows: list[dict[str, Any]],
        symbol: str,
    ) -> dict[str, Any] | None:
        target = symbol.casefold()
        return next(
            (
                row
                for row in rows
                if str(row.get("symbol", "")).casefold() == target
            ),
            None,
        )

    @classmethod
    def _funding_for_exchange(
        cls,
        rows: list[dict[str, Any]],
        exchange: str,
    ) -> Decimal | None:
        row = cls._find_exchange(rows, exchange)
        if row is None:
            return None
        return cls._decimal_or_none(row.get("funding_rate"))

    @staticmethod
    def _decimal_or_none(value: Any) -> Decimal | None:
        if value is None or value == "":
            return None
        return Decimal(str(value))
