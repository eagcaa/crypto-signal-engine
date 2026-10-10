from decimal import Decimal

import aiohttp

from crypto_signal_engine.paper.models import PaperAccountSnapshot, PaperPosition
from crypto_signal_engine.predictions.models import Prediction, PredictionEvaluation


class TelegramNotifier:
    """Small optional Telegram Bot API client for live research alerts."""

    BASE_URL = "https://api.telegram.org"

    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        *,
        timeout_seconds: float = 10.0,
    ) -> None:
        if not bot_token.strip():
            raise ValueError("bot_token is required")
        if not chat_id.strip():
            raise ValueError("chat_id is required")

        self._bot_token = bot_token.strip()
        self._chat_id = chat_id.strip()
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)

    async def send(self, text: str) -> None:
        payload = {
            "chat_id": self._chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }

        async with aiohttp.ClientSession(timeout=self._timeout) as session:
            async with session.post(
                f"{self.BASE_URL}/bot{self._bot_token}/sendMessage",
                json=payload,
            ) as response:
                body = await response.json(content_type=None)
                if response.status >= 400:
                    raise RuntimeError(
                        f"Telegram HTTP {response.status}: {body}"
                    )
                if isinstance(body, dict) and body.get("ok") is False:
                    raise RuntimeError(f"Telegram API error: {body}")

    @staticmethod
    def prediction_text(
        prediction: Prediction,
        *,
        calibrated_confidence: Decimal | None = None,
    ) -> str:
        confidence = (
            "not calibrated"
            if calibrated_confidence is None
            else f"{calibrated_confidence:.2f}%"
        )
        horizon_minutes = prediction.horizon_seconds // 60
        return (
            f"{prediction.symbol}\n"
            f"{horizon_minutes}m: {prediction.direction.value.upper()}\n"
            f"Score: {prediction.raw_score:+.4f}\n"
            f"Confidence: {confidence}\n"
            f"Entry: {prediction.entry_price}\n"
            f"Reason: {prediction.reason or 'n/a'}"
        )

    @staticmethod
    def evaluation_text(
        evaluation: PredictionEvaluation,
        *,
        symbol: str,
        horizon_seconds: int,
        direction: str,
    ) -> str:
        return_pct = (
            "n/a"
            if evaluation.return_pct is None
            else f"{evaluation.return_pct:+.4f}%"
        )
        return (
            f"{symbol}\n"
            f"{horizon_seconds // 60}m {direction.upper()} evaluated\n"
            f"Outcome: {evaluation.outcome.value}\n"
            f"Return: {return_pct}\n"
            f"Success: {evaluation.success}"
        )

    @staticmethod
    def paper_account_text(
        snapshot: PaperAccountSnapshot,
        *,
        position: PaperPosition | None = None,
    ) -> str:
        position_text = ""
        if position is not None:
            position_text = (
                f"\nPosition: {position.direction.upper()} "
                f"{position.symbol} "
                f"PnL={position.pnl if position.pnl is not None else 'open'}"
            )

        win_rate = (
            "n/a"
            if snapshot.win_rate is None
            else f"{snapshot.win_rate:.2f}%"
        )
        return (
            "Paper account\n"
            f"Equity: {snapshot.equity:.2f}\n"
            f"Realized PnL: {snapshot.realized_pnl:+.2f}\n"
            f"Win rate: {win_rate}\n"
            f"Max drawdown: {snapshot.max_drawdown_pct:.2f}%\n"
            f"Halted: {snapshot.trading_halted}"
            f"{position_text}"
        )


    @staticmethod
    def candidate_open_text(
        prediction: Prediction,
        *,
        candidate_name: str,
    ) -> str:
        return (
            "EARLY CANDIDATE - PAPER ONLY\n"
            f"{candidate_name}\n"
            f"{prediction.symbol} {prediction.direction.value.upper()}\n"
            f"Entry: {prediction.entry_price}\n"
            f"TP: +{prediction.take_profit_pct:.4f}%\n"
            f"SL: -{prediction.stop_loss_pct:.4f}%\n"
            f"Score: {prediction.raw_score:+.4f}"
        )

    @staticmethod
    def candidate_close_text(
        position: PaperPosition,
        *,
        candidate_name: str,
    ) -> str:
        return_pct = (
            "n/a"
            if position.return_pct is None
            else f"{position.return_pct:+.4f}%"
        )
        pnl = "n/a" if position.pnl is None else f"{position.pnl:+.2f}"
        return (
            "CANDIDATE RESULT - PAPER ONLY\n"
            f"{candidate_name}\n"
            f"{position.symbol} {position.direction.upper()}\n"
            f"Result: {position.close_reason or position.status.value}\n"
            f"Return: {return_pct}\n"
            f"PnL: {pnl}"
        )

    @staticmethod
    def source_freshness_text(
        *,
        symbol: str,
        source: str,
        kind: str,
        age_ms: int | None,
        bad_intervals: int,
    ) -> str:
        title = (
            "Market source stale"
            if kind == "stale"
            else "Market source recovered"
        )
        age = "missing" if age_ms is None else f"{age_ms} ms"
        return (
            f"{title}\n"
            f"{symbol}\n"
            f"Source: {source.replace('_', ' ').title()}\n"
            f"Age: {age}\n"
            f"Consecutive bad intervals: {bad_intervals}"
        )

    @staticmethod
    def data_quality_text(
        *,
        symbol: str,
        kind: str,
        data_quality: Decimal,
        bad_intervals: int,
    ) -> str:
        title = (
            "Market data degraded"
            if kind == "degraded"
            else "Market data recovered"
        )
        return (
            f"{title}\n"
            f"{symbol}\n"
            f"Quality: {data_quality:.2f}\n"
            f"Consecutive bad intervals: {bad_intervals}"
        )
