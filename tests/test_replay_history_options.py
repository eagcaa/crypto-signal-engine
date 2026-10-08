import pytest

from crypto_signal_engine.examples.replay_history import validate_options


def test_calibration_artifact_requires_exact_trade_replay() -> None:
    with pytest.raises(ValueError):
        validate_options(
            exact_binance_trades=False,
            write_calibration_path="runtime-data/calibration.json",
        )


def test_exact_trade_replay_allows_calibration_artifact() -> None:
    validate_options(
        exact_binance_trades=True,
        write_calibration_path="runtime-data/calibration.json",
    )


def test_sampled_replay_without_calibration_artifact_is_allowed() -> None:
    validate_options(
        exact_binance_trades=False,
        write_calibration_path="",
    )
