from crypto_signal_engine.examples.replay_history import parse_args


def test_parse_args_accepts_end_offset_hours(monkeypatch) -> None:
    monkeypatch.setattr(
        "sys.argv",
        [
            "replay_history",
            "--hours",
            "12",
            "--end-offset-hours",
            "12",
            "--compare-price-sources",
        ],
    )

    args = parse_args()

    assert args.hours == 12.0
    assert args.end_offset_hours == 12.0
    assert args.compare_price_sources is True
