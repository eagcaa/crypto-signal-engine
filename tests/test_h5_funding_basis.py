import zipfile
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from crypto_signal_engine.examples.backfill_h5_funding_basis import (
    Month,
    fred_dtb3_url,
    monthly_archive_specs,
    validate_development_range,
    validate_fred_csv,
)
from crypto_signal_engine.examples.h5_funding_basis_baseline import (
    CostModel,
    FundingPoint,
    PricePoint,
    load_kline_prices,
    simulate,
)


def test_h5_downloader_refuses_final_test_period() -> None:
    with pytest.raises(ValueError, match="development-only"):
        validate_development_range(
            start_day=date(2024, 1, 1),
            end_day=date(2024, 1, 31),
        )


def test_h5_monthly_archive_paths_are_frozen_sources() -> None:
    specs = monthly_archive_specs(
        symbol="btcusdt",
        month=Month(2020, 1),
    )
    urls = {item.dataset: item.url for item in specs}

    assert urls["futures_um_fundingRate"].endswith(
        "/futures/um/monthly/fundingRate/BTCUSDT/"
        "BTCUSDT-fundingRate-2020-01.zip"
    )
    assert urls["spot_klines_1h"].endswith(
        "/spot/monthly/klines/BTCUSDT/1h/"
        "BTCUSDT-1h-2020-01.zip"
    )
    assert urls["futures_um_klines_1h"].endswith(
        "/futures/um/monthly/klines/BTCUSDT/1h/"
        "BTCUSDT-1h-2020-01.zip"
    )


def test_h5_fred_validator_accepts_observation_date_header(
    tmp_path: Path,
) -> None:
    path = tmp_path / "DTB3.csv"
    path.write_text(
        "observation_date,DTB3\n2019-12-31,1.52\n",
        encoding="utf-8",
    )

    validate_fred_csv(path)


def test_h5_fred_validator_accepts_legacy_date_header(
    tmp_path: Path,
) -> None:
    path = tmp_path / "DTB3.csv"
    path.write_text(
        "DATE,DTB3\n2019-12-31,1.52\n",
        encoding="utf-8",
    )

    validate_fred_csv(path)


def test_h5_fred_validator_rejects_invalid_date_header(
    tmp_path: Path,
) -> None:
    path = tmp_path / "DTB3.csv"
    path.write_text(
        "foo,DTB3\n2019-12-31,1.52\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Unexpected DTB3 CSV header"):
        validate_fred_csv(path)


def test_h5_fred_url_is_bounded_before_final_test() -> None:
    url = fred_dtb3_url()
    assert "cosd=2019-12-01" in url
    assert "coed=2023-12-31" in url
    assert "2024" not in url
    assert "2025" not in url


def test_h5_kline_loader_refuses_2024_rows(tmp_path: Path) -> None:
    path = tmp_path / "BTCUSDT-1h-2024-01.zip"
    csv_name = "BTCUSDT-1h-2024-01.csv"
    open_time = int(
        datetime(2024, 1, 1, tzinfo=UTC).timestamp() * 1000
    )
    close_time = open_time + 3_599_999

    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            csv_name,
            ",".join(
                [
                    str(open_time),
                    "100",
                    "101",
                    "99",
                    "100",
                    "10",
                    str(close_time),
                    "1000",
                    "10",
                    "5",
                    "500",
                    "0",
                ]
            )
            + "\n",
        )

    with pytest.raises(RuntimeError, match="refuses non-development"):
        load_kline_prices([path])


def _hourly_prices(
    *,
    start: datetime,
    hours: int,
    start_price: Decimal,
    end_price: Decimal,
) -> list[PricePoint]:
    output: list[PricePoint] = []
    for index in range(hours):
        fraction = (
            Decimal(index) / Decimal(max(hours - 1, 1))
        )
        price = start_price + (
            end_price - start_price
        ) * fraction
        output.append(
            PricePoint(
                timestamp=start + timedelta(hours=index),
                close=price,
            )
        )
    return output


def test_h5_baseline_rebalances_on_margin_buffer_breach() -> None:
    start = datetime(2020, 1, 1, tzinfo=UTC)
    spot = _hourly_prices(
        start=start,
        hours=49,
        start_price=Decimal("10000"),
        end_price=Decimal("17000"),
    )
    perp = list(spot)
    funding = [
        FundingPoint(
            timestamp=start + timedelta(hours=8 * index),
            funding_rate=Decimal("0"),
            interval_hours=Decimal("8"),
        )
        for index in range(1, 6)
    ]
    zero_cost = CostModel(
        name="zero",
        spot_execution_rate=Decimal("0"),
        futures_execution_rate=Decimal("0"),
    )

    result = simulate(
        leverage=Decimal("1"),
        spot_prices=spot,
        perp_prices=perp,
        funding=funding,
        cost_model=zero_cost,
    )

    assert result.rebalances >= 1
    assert result.rebalanced_btc > 0
    assert result.transferred_to_margin > 0


def test_h5_positive_funding_is_income_to_short() -> None:
    start = datetime(2020, 1, 1, tzinfo=UTC)
    spot = _hourly_prices(
        start=start,
        hours=49,
        start_price=Decimal("10000"),
        end_price=Decimal("10000"),
    )
    perp = list(spot)
    funding = [
        FundingPoint(
            timestamp=start + timedelta(hours=8 * index),
            funding_rate=Decimal("0.001"),
            interval_hours=Decimal("8"),
        )
        for index in range(1, 6)
    ]
    zero_cost = CostModel(
        name="zero",
        spot_execution_rate=Decimal("0"),
        futures_execution_rate=Decimal("0"),
    )

    result = simulate(
        leverage=Decimal("1"),
        spot_prices=spot,
        perp_prices=perp,
        funding=funding,
        cost_model=zero_cost,
    )

    assert result.funding_income == Decimal("50.000")
    assert result.ending_equity == result.starting_capital + Decimal("50.000")
