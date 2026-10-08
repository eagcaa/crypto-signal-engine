from crypto_signal_engine.replay.compare import compare_replay_reports
from crypto_signal_engine.replay.report import ReplayReport, ReplayStats


def make_report(*, tp: int, sl: int, no_touch: int) -> ReplayReport:
    stats = ReplayStats(
        predictions=tp + sl + no_touch,
        take_profit=tp,
        stop_loss=sl,
        expired_no_touch=no_touch,
        expired_without_data=0,
    )
    return ReplayReport(
        by_horizon={300: stats},
        by_regime=(),
        by_score_bin=(),
    )


def test_compare_replay_reports_shows_first_touch_deltas() -> None:
    rows = compare_replay_reports(
        make_report(tp=4, sl=2, no_touch=4),
        make_report(tp=5, sl=3, no_touch=2),
    )

    assert len(rows) == 1
    row = rows[0]
    assert row.horizon_seconds == 300
    assert row.take_profit_delta == 1
    assert row.stop_loss_delta == 1
    assert row.no_touch_delta == -2
