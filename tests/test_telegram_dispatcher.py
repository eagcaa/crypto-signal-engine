import asyncio

import pytest

from crypto_signal_engine.integrations.telegram_dispatcher import TelegramDispatcher


class FakeSender:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send(self, text: str) -> None:
        await asyncio.sleep(0)
        self.messages.append(text)


@pytest.mark.asyncio
async def test_dispatcher_sends_queued_messages_in_order() -> None:
    sender = FakeSender()
    dispatcher = TelegramDispatcher(sender)
    await dispatcher.start()

    assert dispatcher.enqueue("first") is True
    assert dispatcher.enqueue("second") is True

    await dispatcher.stop()

    assert sender.messages == ["first", "second"]


@pytest.mark.asyncio
async def test_dispatcher_rejects_enqueue_before_start() -> None:
    dispatcher = TelegramDispatcher(FakeSender())

    assert dispatcher.enqueue("message") is False


def test_dispatcher_validates_queue_size() -> None:
    with pytest.raises(ValueError):
        TelegramDispatcher(FakeSender(), max_queue_size=0)
