from dataclasses import dataclass
from uuid import uuid4

from crypto_signal_engine.features.research import ResearchFeatureSnapshot
from crypto_signal_engine.paper.broker import PaperBroker
from crypto_signal_engine.predictions.models import Prediction
from crypto_signal_engine.replay.candidate_gates import (
    CANDIDATE_GATES,
    CandidateGate,
    prediction_passes_gate,
)


@dataclass(frozen=True, slots=True)
class ForwardCandidate:
    gate: CandidateGate
    model_name: str


class CandidatePaperTracker:
    """Turn a frozen replay candidate into an isolated forward paper stream.

    The tracker never sends exchange orders. It only clones a base prediction
    after the frozen gate passes, assigns a separate model name, and applies
    the candidate's frozen TP/SL so its forward history cannot mix with V4.
    """

    MODEL_PREFIX = "candidate_paper_v5"

    def __init__(
        self,
        broker: PaperBroker,
        *,
        candidate_name: str = "15m_long_range_high",
    ) -> None:
        gate = next(
            (item for item in CANDIDATE_GATES if item.name == candidate_name),
            None,
        )
        if gate is None:
            raise ValueError(f"Unknown candidate gate: {candidate_name}")
        if (
            gate.frozen_take_profit_pct is None
            or gate.frozen_stop_loss_pct is None
        ):
            raise ValueError(
                f"Candidate gate is not frozen: {candidate_name}"
            )

        self._broker = broker
        self._candidate = ForwardCandidate(
            gate=gate,
            model_name=f"{self.MODEL_PREFIX}_{gate.name}",
        )

    @property
    def broker(self) -> PaperBroker:
        return self._broker

    @property
    def model_name(self) -> str:
        return self._candidate.model_name

    @property
    def gate(self) -> CandidateGate:
        return self._candidate.gate

    def candidate_prediction(
        self,
        prediction: Prediction,
        feature_snapshot: ResearchFeatureSnapshot,
    ) -> Prediction | None:
        if not prediction_passes_gate(
            prediction,
            self._candidate.gate,
            feature_snapshot,
        ):
            return None

        return Prediction(
            id=uuid4(),
            symbol=prediction.symbol,
            created_at=prediction.created_at,
            expires_at=prediction.expires_at,
            horizon_seconds=prediction.horizon_seconds,
            direction=prediction.direction,
            entry_price=prediction.entry_price,
            raw_score=prediction.raw_score,
            data_quality=prediction.data_quality,
            take_profit_pct=self._candidate.gate.frozen_take_profit_pct,
            stop_loss_pct=self._candidate.gate.frozen_stop_loss_pct,
            model_name=self._candidate.model_name,
            feature_contributions=prediction.feature_contributions,
            reason=(
                f"forward_candidate={self._candidate.gate.name}; "
                f"base_model={prediction.model_name}; "
                f"{prediction.reason or ''}"
            ).rstrip("; "),
        )
