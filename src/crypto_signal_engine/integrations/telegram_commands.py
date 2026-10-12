"""Telegram long-poll command reader for on-demand coin analysis."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import aiohttp

from crypto_signal_engine.watch.coin_analysis import CoinAnalysis, CoinAnalyzer


class TelegramCommandPoller:
    BASE_URL = "https://api.telegram.org"

    def __init__(
        self,
        *,
        bot_token: str,
        allowed_chat_id: str,
        analyzer: CoinAnalyzer,
        extra_allowed_chat_ids: tuple[str, ...] = (),
        on_analysis: Callable[[CoinAnalysis], Awaitable[None]] | None = None,
        timeout_seconds: float = 35.0,
    ) -> None:
        self._bot_token = bot_token.strip()
        self._allowed_chat_ids = {
            str(item).strip()
            for item in (allowed_chat_id, *extra_allowed_chat_ids)
            if str(item).strip()
        }
        self._analyzer = analyzer
        self._on_analysis = on_analysis
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._offset: int | None = None

    async def run(self) -> None:
        while True:
            try:
                updates = await self._get_updates()
                for update in updates:
                    update_id = update.get("update_id")
                    if isinstance(update_id, int):
                        self._offset = update_id + 1
                    await self._handle(update)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                print(
                    "TELEGRAM command polling unavailable: "
                    f"{type(exc).__name__}: {exc}"
                )
                await asyncio.sleep(3)

    async def _get_updates(self) -> list[dict[str, Any]]:
        params = {"timeout": "25"}
        if self._offset is not None:
            params["offset"] = str(self._offset)
        async with aiohttp.ClientSession(timeout=self._timeout) as session:
            async with session.get(
                f"{self.BASE_URL}/bot{self._bot_token}/getUpdates",
                params=params,
            ) as response:
                payload = await response.json(content_type=None)
                if response.status >= 400:
                    raise RuntimeError(
                        f"Telegram HTTP {response.status}: {payload}"
                    )
        result = payload.get("result", []) if isinstance(payload, dict) else []
        return [item for item in result if isinstance(item, dict)]

    async def _handle(self, update: dict[str, Any]) -> None:
        message = update.get("message")
        if not isinstance(message, dict):
            return
        chat = message.get("chat")
        text = message.get("text")
        if not isinstance(chat, dict) or not isinstance(text, str):
            return
        chat_id = str(chat.get("id", ""))
        if chat_id not in self._allowed_chat_ids:
            print(f"TELEGRAM command ignored from unauthorized chat {chat_id}")
            return

        request = self._parse_request(text)
        if request is None:
            return

        try:
            analysis = await self._analyzer.analyze(request)
            if self._on_analysis is not None:
                try:
                    await self._on_analysis(analysis)
                except Exception as exc:  # noqa: BLE001
                    print(
                        "RANGE forecast persistence unavailable: "
                        f"{type(exc).__name__}: {exc}"
                    )
            await self._send(chat_id, analysis.text)
        except Exception as exc:  # noqa: BLE001
            await self._send(
                chat_id,
                f"⚠️ {request.upper()} analizi alınamadı: "
                f"{type(exc).__name__}: {exc}",
            )

    async def _send(self, chat_id: str, text: str) -> None:
        async with aiohttp.ClientSession(timeout=self._timeout) as session:
            async with session.post(
                f"{self.BASE_URL}/bot{self._bot_token}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "disable_web_page_preview": True,
                },
            ) as response:
                payload = await response.json(content_type=None)
                if response.status >= 400:
                    raise RuntimeError(
                        f"Telegram HTTP {response.status}: {payload}"
                    )

    @staticmethod
    def _parse_request(text: str) -> str | None:
        stripped = text.strip()
        value = stripped.split("@", 1)[0] if stripped.startswith("/") else stripped
        if not value:
            return None
        if value.lower() in {"/start", "/help", "/analyze"}:
            return None
        if value.lower().startswith("/analyze "):
            return value.split(maxsplit=1)[1].strip()
        if value.startswith("/"):
            return value[1:].strip()
        # Plain ticker input: BTC, BTCUSDT, PYTH, PENDLE...
        compact = value.replace("/", "").replace("-", "").replace(" ", "")
        if 2 <= len(compact) <= 20 and compact.isalnum():
            return compact
        return None
