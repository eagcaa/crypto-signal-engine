from crypto_signal_engine.paper.broker import PaperBroker
from crypto_signal_engine.paper.models import PaperAccountSnapshot, PaperRiskConfig
from crypto_signal_engine.replay.models import ReplayResult


def simulate_replay(
    result: ReplayResult,
    *,
    risk_config: PaperRiskConfig | None = None,
) -> PaperAccountSnapshot:
    """Replay predictions/evaluations through the isolated paper broker."""

    broker = PaperBroker(risk_config)

    prediction_events = {
        prediction.id: prediction
        for prediction in result.predictions
    }
    evaluation_events = {
        evaluation.prediction_id: evaluation
        for evaluation in result.evaluations
    }

    events = []
    for prediction in prediction_events.values():
        events.append((prediction.created_at, 0, prediction.id))
    for evaluation in evaluation_events.values():
        events.append((evaluation.evaluated_at, 1, evaluation.prediction_id))
    events.sort(key=lambda item: (item[0], item[1]))

    for _, event_type, prediction_id in events:
        if event_type == 0:
            broker.open_from_prediction(prediction_events[prediction_id])
        else:
            broker.apply_evaluation(evaluation_events[prediction_id])

    return broker.snapshot()
