"""Sample SMA crossover strategy demonstrating the pyfx strategy interface."""

from decimal import Decimal

from nautilus_trader.indicators import SimpleMovingAverage
from nautilus_trader.model.data import Bar

from pyfx.strategies.base import PyfxStrategy, PyfxStrategyConfig


class SMACrossConfig(PyfxStrategyConfig, frozen=True):
    """Configuration for the SMA crossover strategy."""

    fast_period: int = 10
    slow_period: int = 50
    trade_size: Decimal = Decimal("100000")


class SMACrossStrategy(PyfxStrategy):
    """Simple Moving Average crossover strategy.

    Goes long when the fast SMA crosses above the slow SMA.
    Goes short when the fast SMA crosses below the slow SMA.
    """

    def __init__(self, config: SMACrossConfig) -> None:
        super().__init__(config)
        self.fast_sma = SimpleMovingAverage(config.fast_period)
        self.slow_sma = SimpleMovingAverage(config.slow_period)

    @classmethod
    def chart_indicators(cls) -> list[dict[str, object]]:
        return [
            {"name": "sma", "period": 10},
            {"name": "sma", "period": 50},
        ]

    def on_start(self) -> None:
        self.register_indicator_for_bars(self.config.bar_type, self.fast_sma)
        self.register_indicator_for_bars(self.config.bar_type, self.slow_sma)
        self.subscribe_bars(self.config.bar_type)

    def on_bar(self, bar: Bar) -> None:
        if not self.indicators_initialized():
            return

        fast = self.fast_sma.value
        slow = self.slow_sma.value

        if fast >= slow:
            if self.flat():
                self.market_buy()
            elif self.is_short():
                self.close_all()
                self.market_buy()
        elif fast < slow:  # pragma: no branch
            if self.flat():
                self.market_sell()
            elif self.is_long():
                self.close_all()
                self.market_sell()
