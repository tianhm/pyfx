"""Base strategy class wrapping NautilusTrader's Strategy."""

from decimal import Decimal

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.trading.strategy import Strategy


class PyfxStrategyConfig(StrategyConfig, frozen=True):
    """Base config that all pyfx strategies should extend."""

    instrument_id: InstrumentId
    bar_type: BarType
    extra_bar_types: tuple[BarType, ...] = ()
    trade_size: Decimal = Decimal("100000")


class PyfxStrategy(Strategy):
    """Base strategy that all pyfx strategies should extend.

    Provides convenience methods for common trading operations.
    Subclasses must implement on_bar() at minimum.
    """

    def __init__(self, config: PyfxStrategyConfig) -> None:
        super().__init__(config)

    # -- Convenience helpers --------------------------------------------------

    def market_buy(self, size: Decimal | None = None) -> None:
        """Submit a market buy order."""
        instrument = self.cache.instrument(self.config.instrument_id)
        qty = instrument.make_qty(size or self.config.trade_size)
        order = self.order_factory.market(
            self.config.instrument_id,
            OrderSide.BUY,
            qty,
        )
        self.submit_order(order)

    def market_sell(self, size: Decimal | None = None) -> None:
        """Submit a market sell order."""
        instrument = self.cache.instrument(self.config.instrument_id)
        qty = instrument.make_qty(size or self.config.trade_size)
        order = self.order_factory.market(
            self.config.instrument_id,
            OrderSide.SELL,
            qty,
        )
        self.submit_order(order)

    def flat(self) -> bool:
        """Check if we have no open position."""
        return self.portfolio.is_flat(self.config.instrument_id)

    def is_long(self) -> bool:
        return self.portfolio.is_net_long(self.config.instrument_id)

    def is_short(self) -> bool:
        return self.portfolio.is_net_short(self.config.instrument_id)

    def close_all(self) -> None:
        """Close all positions for our instrument."""
        self.close_all_positions(self.config.instrument_id)

    # -- Chart indicator defaults -----------------------------------------------

    @classmethod
    def chart_indicators(cls) -> list[dict[str, object]]:
        """Return default indicators to display on the backtest chart.

        Override in subclasses to provide strategy-specific defaults.
        Each entry should have ``name`` (sma, ema, rsi, macd, atr) and
        ``period`` (int).  Optional ``pane`` key: ``"main"`` for overlays,
        ``"below"`` for separate panes (auto-detected if omitted).
        """
        return []

    # -- Lifecycle hooks for subclasses ---------------------------------------

    def on_start(self) -> None:
        """Override in subclass. Called when strategy starts."""

    def on_bar(self, bar: Bar) -> None:
        """Override in subclass. Called on each new bar."""

    def on_stop(self) -> None:
        """Default: close all positions when strategy stops."""
        self.close_all()
