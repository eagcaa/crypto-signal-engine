from decimal import Decimal

from crypto_signal_engine.paper.models import PaperRiskConfig


class PaperRiskManager:
    """Conservative paper-only position sizing.

    Position size is bounded both by the maximum loss allowed at the configured
    stop and by a maximum fraction of account equity. No leverage is assumed.
    """

    def __init__(self, config: PaperRiskConfig | None = None) -> None:
        self.config = config or PaperRiskConfig()
        self._validate(self.config)

    def position_notional(
        self,
        *,
        equity: Decimal,
    ) -> Decimal:
        if equity <= 0:
            return Decimal("0")

        risk_budget = (
            equity
            * self.config.risk_per_trade_pct
            / Decimal("100")
        )
        stop_fraction = self.config.stop_loss_pct / Decimal("100")
        risk_sized_notional = (
            risk_budget / stop_fraction
            if stop_fraction > 0
            else Decimal("0")
        )
        notional_cap = (
            equity
            * self.config.max_notional_pct
            / Decimal("100")
        )

        return max(
            Decimal("0"),
            min(risk_sized_notional, notional_cap),
        )

    @staticmethod
    def _validate(config: PaperRiskConfig) -> None:
        if config.starting_equity <= 0:
            raise ValueError("starting_equity must be positive")
        if not Decimal("0") < config.risk_per_trade_pct <= Decimal("100"):
            raise ValueError("risk_per_trade_pct must be in (0, 100]")
        if not Decimal("0") < config.max_notional_pct <= Decimal("100"):
            raise ValueError("max_notional_pct must be in (0, 100]")
        if config.max_open_positions <= 0:
            raise ValueError("max_open_positions must be positive")
        if not Decimal("0") < config.stop_loss_pct <= Decimal("100"):
            raise ValueError("stop_loss_pct must be in (0, 100]")
        if not Decimal("0") < config.max_drawdown_pct <= Decimal("100"):
            raise ValueError("max_drawdown_pct must be in (0, 100]")
        if config.max_consecutive_losses <= 0:
            raise ValueError("max_consecutive_losses must be positive")
        if not Decimal("0") <= config.fee_pct_per_side <= Decimal("5"):
            raise ValueError("fee_pct_per_side must be in [0, 5]")
        if not Decimal("0") <= config.slippage_pct_per_side <= Decimal("5"):
            raise ValueError("slippage_pct_per_side must be in [0, 5]")
