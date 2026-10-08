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
