import asyncio
from collections.abc import Awaitable
from typing import Protocol


class _TelegramSender(Protocol):
    def send(self, text: str) -> Awaitable[None]:
        ...


class TelegramDispatcher:
    """Serialize Telegram sends on a background task so market loops never wait."""

    def __init__(
        self,
        sender: _TelegramSender,
        *,
        max_queue_size: int = 100,
    ) -> None:
        if max_queue_size <= 0:
            raise ValueError("max_queue_size must be positive")

        self._sender = sender
        self._queue: asyncio.Queue[str | None] = asyncio.Queue(
            maxsize=max_queue_size
        )
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._worker())

    async def stop(self) -> None:
        if self._task is None:
            return

        await self._queue.put(None)
        await self._task
        self._task = None

    def enqueue(self, text: str) -> bool:
        if self._task is None:
            return False

        try:
            self._queue.put_nowait(text)
            return True
        except asyncio.QueueFull:
            return False

    async def _worker(self) -> None:
        while True:
            text = await self._queue.get()
            try:
                if text is None:
                    return
                await self._sender.send(text)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(
                    "TELEGRAM unavailable: "
                    f"{type(exc).__name__}: {exc}"
                )
            finally:
                self._queue.task_done()
