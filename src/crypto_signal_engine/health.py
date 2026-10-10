from dataclasses import dataclass
from decimal import Decimal

from crypto_signal_engine.market.snapshot import MarketSnapshot


@dataclass(frozen=True, slots=True)
class DataQualityEvent:
    kind: str
    data_quality: Decimal
    bad_intervals: int


class DataQualityMonitor:
    """Emit degraded/recovered transitions without alert spam."""

    def __init__(
        self,
        *,
        minimum_quality: Decimal = Decimal("0.67"),
        bad_intervals_before_alert: int = 3,
    ) -> None:
        if not Decimal("0") <= minimum_quality <= Decimal("1"):
            raise ValueError("minimum_quality must be in [0, 1]")
        if bad_intervals_before_alert <= 0:
            raise ValueError("bad_intervals_before_alert must be positive")

        self.minimum_quality = minimum_quality
        self.bad_intervals_before_alert = bad_intervals_before_alert
        self._bad_intervals = 0
        self._degraded = False

    def observe(
        self,
        snapshot: MarketSnapshot,
    ) -> DataQualityEvent | None:
        if snapshot.data_quality < self.minimum_quality:
            self._bad_intervals += 1

            if (
                not self._degraded
                and self._bad_intervals >= self.bad_intervals_before_alert
            ):
                self._degraded = True
                return DataQualityEvent(
                    kind="degraded",
                    data_quality=snapshot.data_quality,
                    bad_intervals=self._bad_intervals,
                )

            return None

        previous_bad_intervals = self._bad_intervals
        self._bad_intervals = 0

        if self._degraded:
            self._degraded = False
            return DataQualityEvent(
                kind="recovered",
                data_quality=snapshot.data_quality,
                bad_intervals=previous_bad_intervals,
            )

        return None



@dataclass(frozen=True, slots=True)
class SourceFreshnessEvent:
    source: str
    kind: str
    age_ms: int | None
    bad_intervals: int


class SourceFreshnessMonitor:
    """Emit stale/recovered transitions independently for each live source."""

    _AGE_FIELDS = {
        "binance_spot": "binance_spot_age_ms",
        "binance_futures": "binance_futures_age_ms",
        "bybit_spot": "bybit_spot_age_ms",
        "bybit_futures": "bybit_futures_age_ms",
        "binance_book": "binance_book_age_ms",
        "bybit_book": "bybit_book_age_ms",
    }

    def __init__(
        self,
        *,
        stale_after_ms: int = 30000,
        bad_intervals_before_alert: int = 3,
    ) -> None:
        if stale_after_ms <= 0:
            raise ValueError("stale_after_ms must be positive")
        if bad_intervals_before_alert <= 0:
            raise ValueError("bad_intervals_before_alert must be positive")

        self.stale_after_ms = stale_after_ms
        self.bad_intervals_before_alert = bad_intervals_before_alert
        self._bad_intervals = {source: 0 for source in self._AGE_FIELDS}
        self._stale = {source: False for source in self._AGE_FIELDS}

    def observe(
        self,
        snapshot: MarketSnapshot,
    ) -> tuple[SourceFreshnessEvent, ...]:
        events: list[SourceFreshnessEvent] = []

        for source, field_name in self._AGE_FIELDS.items():
            age_ms = getattr(snapshot, field_name)
            stale_now = age_ms is None or age_ms > self.stale_after_ms

            if stale_now:
                self._bad_intervals[source] += 1
                if (
                    not self._stale[source]
                    and self._bad_intervals[source] >= self.bad_intervals_before_alert
                ):
                    self._stale[source] = True
                    events.append(
                        SourceFreshnessEvent(
                            source=source,
                            kind="stale",
                            age_ms=age_ms,
                            bad_intervals=self._bad_intervals[source],
                        )
                    )
                continue

            previous_bad_intervals = self._bad_intervals[source]
            self._bad_intervals[source] = 0
            if self._stale[source]:
                self._stale[source] = False
                events.append(
                    SourceFreshnessEvent(
                        source=source,
                        kind="recovered",
                        age_ms=age_ms,
                        bad_intervals=previous_bad_intervals,
                    )
                )

        return tuple(events)
