import json
from pathlib import Path

import pytest

from crypto_signal_engine.db.models import ResearchFeatureSnapshotRow
from crypto_signal_engine.examples.materialize_history_v2 import (
    load_alignment_shift,
)


def test_v2_alignment_artifact_must_be_pass(tmp_path: Path) -> None:
    path = tmp_path / "alignment.json"
    path.write_text(
        json.dumps(
            {
                "status": "AMBIGUOUS",
                "symbol": "BTCUSDT",
                "selected_shift_minutes": None,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="blocked"):
        load_alignment_shift(
            artifact_path=path,
            symbol="BTCUSDT",
        )


def test_v2_alignment_artifact_returns_validated_shift(
    tmp_path: Path,
) -> None:
    path = tmp_path / "alignment.json"
    path.write_text(
        json.dumps(
            {
                "status": "PASS",
                "symbol": "BTCUSDT",
                "selected_shift_minutes": 5,
            }
        ),
        encoding="utf-8",
    )

    assert load_alignment_shift(
        artifact_path=path,
        symbol="BTCUSDT",
    ) == 5


def test_research_feature_primary_key_includes_provenance() -> None:
    primary_key_columns = tuple(
        ResearchFeatureSnapshotRow.__table__.primary_key.columns.keys()
    )
    assert primary_key_columns == (
        "timestamp",
        "symbol",
        "dataset_provenance",
    )
