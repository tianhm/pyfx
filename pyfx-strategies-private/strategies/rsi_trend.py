"""RSI Trend strategy - ported from old CobanBase/CobanStrategy concepts.

Uses RSI to detect trend direction and enters on pullbacks.
This demonstrates a more complex strategy with multiple indicators.
"""

from decimal import Decimal

from nautilus_trader.indicators import ExponentialMovingAverage, RelativeStrengthIndex
from nautilus_trader.model.data import Bar

from pyfx.strategies.base import PyfxStrategy, PyfxStrategyConfig


class RSITrendConfig(PyfxStrategyConfig, frozen=True):
    """Configuration for RSI Trend strategy."""

    rsi_period: int = 14
    ema_period: int = 50
    rsi_oversold: float = 30.0
    rsi_overbought: float = 70.0
    trade_size: Decimal = Decimal("100000")


class RSITrendStrategy(PyfxStrategy):
    """RSI Trend strategy.

    Enters long when RSI crosses above oversold level and price is above EMA.
    Enters short when RSI crosses below overbought level and price is below EMA.
    Exits when RSI reaches the opposite extreme.
    """

    def __init__(self, config: RSITrendConfig) -> None:
        super().__init__(config)
        self.rsi = RelativeStrengthIndex(config.rsi_period)
        self.ema = ExponentialMovingAverage(config.ema_period)
        self._prev_rsi: float = 50.0

    def on_start(self) -> None:
        self.register_indicator_for_bars(self.config.bar_type, self.rsi)
        self.register_indicator_for_bars(self.config.bar_type, self.ema)
        self.subscribe_bars(self.config.bar_type)

    def on_bar(self, bar: Bar) -> None:
        if not self.indicators_initialized():
            return

        rsi_val = self.rsi.value
        ema_val = self.ema.value
        price = float(bar.close)
        cfg: RSITrendConfig = self.config

        if self.flat():
            # Look for entry
            if (
                self._prev_rsi <= cfg.rsi_oversold
                and rsi_val > cfg.rsi_oversold
                and price > ema_val
            ):
                self.market_buy()
            elif (
                self._prev_rsi >= cfg.rsi_overbought
                and rsi_val < cfg.rsi_overbought
                and price < ema_val
            ):
                self.market_sell()
        elif self.is_long():
            # Exit long on RSI overbought
            if rsi_val >= cfg.rsi_overbought:
                self.close_all()
        elif self.is_short():
            # Exit short on RSI oversold
            if rsi_val <= cfg.rsi_oversold:
                self.close_all()

        self._prev_rsi = rsi_val
