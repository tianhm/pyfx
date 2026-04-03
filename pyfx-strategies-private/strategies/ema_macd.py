"""EMA + MACD strategy.

Uses EMA for trend direction and MACD for entry timing.
Inspired by the old NewStrategy's indicator setup (SMA + MACD + RSI).
"""

from decimal import Decimal

from nautilus_trader.indicators import (
    ExponentialMovingAverage,
    MovingAverageConvergenceDivergence,
)
from nautilus_trader.model.data import Bar

from pyfx.strategies.base import PyfxStrategy, PyfxStrategyConfig


class EMAMACDConfig(PyfxStrategyConfig, frozen=True):
    """Configuration for EMA + MACD strategy."""

    ema_period: int = 50
    macd_fast: int = 12
    macd_slow: int = 26
    trade_size: Decimal = Decimal("100000")


class EMAMACDStrategy(PyfxStrategy):
    """EMA + MACD strategy.

    Trend filter: EMA determines if we look for longs or shorts.
    Entry: MACD crosses zero in the trend direction.
    Exit: MACD crosses zero against us.

    Note: NautilusTrader's MACD is the difference between fast and slow MA
    (no separate signal line). We use zero-line crossovers directly.
    """

    def __init__(self, config: EMAMACDConfig) -> None:
        super().__init__(config)
        self.ema = ExponentialMovingAverage(config.ema_period)
        self.macd = MovingAverageConvergenceDivergence(
            config.macd_fast,
            config.macd_slow,
        )
        self._prev_macd: float = 0.0

    def on_start(self) -> None:
        self.register_indicator_for_bars(self.config.bar_type, self.ema)
        self.register_indicator_for_bars(self.config.bar_type, self.macd)
        self.subscribe_bars(self.config.bar_type)

    def on_bar(self, bar: Bar) -> None:
        if not self.indicators_initialized():
            return

        price = float(bar.close)
        macd_val = self.macd.value
        ema_val = self.ema.value
        trend_up = price > ema_val

        if self.flat():
            # MACD crosses above zero in uptrend -> buy
            if trend_up and self._prev_macd <= 0 and macd_val > 0:
                self.market_buy()
            # MACD crosses below zero in downtrend -> sell
            elif not trend_up and self._prev_macd >= 0 and macd_val < 0:
                self.market_sell()
        elif self.is_long():
            if macd_val < 0:
                self.close_all()
        elif self.is_short():
            if macd_val > 0:
                self.close_all()

        self._prev_macd = macd_val
