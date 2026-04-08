"""Shared types and data models."""

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field, model_validator


class BacktestConfig(BaseModel):
    """Configuration for a single backtest run."""

    strategy: str = Field(description="Strategy name (from entry points or directory)")
    instrument: str = Field(description="Instrument ID, e.g. 'EUR/USD'")
    start: datetime
    end: datetime
    bar_type: str = Field(
        default="1-MINUTE-LAST-EXTERNAL",
        description="Bar specification (step-aggregation-price_type-source)",
    )
    extra_bar_types: list[str] = Field(
        default_factory=list,
        description="Additional bar types for multi-timeframe strategies",
    )
    trade_size: Decimal = Field(default=Decimal("100000"))
    balance: float = 100_000.0
    leverage: float = 50.0
    venue: str = "SIM"
    strategy_params: dict[str, bool | int | float | str] = Field(default_factory=dict)
    random_seed: int | None = Field(
        default=42,
        description="Random seed for slippage model (None = random each run)",
    )


def parse_strategy_params(
    params: tuple[str, ...] | dict[str, str],
) -> dict[str, bool | int | float | str]:
    """Parse strategy parameters, coercing values to bool, int, float, or str.

    Accepts either CLI ``key=value`` tuples or a dict of string key-value pairs
    (e.g. from a web form).
    """
    items: list[tuple[str, str]]
    if isinstance(params, dict):
        items = list(params.items())
    else:
        items = []
        for p in params:
            key, _, value = p.partition("=")
            items.append((key, value))

    result: dict[str, bool | int | float | str] = {}
    for key, value in items:
        if value.lower() in ("true", "false"):
            result[key] = value.lower() == "true"
        else:
            try:
                result[key] = int(value)
            except ValueError:
                try:
                    result[key] = float(value)
                except ValueError:
                    result[key] = value
    return result


class TradeRecord(BaseModel):
    """A single closed trade from a backtest."""

    instrument: str
    side: str
    quantity: float
    open_price: float
    close_price: float
    realized_pnl: float
    pnl_currency: str = "USD"
    realized_return_pct: float
    opened_at: datetime
    closed_at: datetime
    duration_seconds: float


class EquityPoint(BaseModel):
    """A single point on the equity curve."""

    timestamp: datetime
    balance: float


class BacktestResult(BaseModel):
    """Full results of a completed backtest."""

    config: BacktestConfig
    total_pnl: float
    total_return_pct: float
    num_trades: int
    win_rate: float
    max_drawdown_pct: float
    sharpe_ratio: float | None = None
    avg_trade_pnl: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    profit_factor: float | None = None
    trades: list[TradeRecord] = Field(default_factory=list)
    equity_curve: list[EquityPoint] = Field(default_factory=list)
    duration_seconds: float = 0.0


# ---------------------------------------------------------------------------
# Live / Paper Trading Types
# ---------------------------------------------------------------------------


class LiveTradingConfig(BaseModel):
    """Configuration for a live/paper trading session."""

    strategy: str = Field(description="Strategy name")
    instrument: str = Field(default="", description="Primary instrument (backward compat)")
    instruments: list[str] = Field(
        default_factory=list,
        description="Instruments to trade (e.g. ['XAU/USD', 'EUR/USD'])",
    )
    bar_type: str = Field(
        default="1-MINUTE-LAST-EXTERNAL",
        description="Primary bar type",
    )
    extra_bar_types: list[str] = Field(
        default_factory=list,
        description="Additional bar types for multi-timeframe strategies",
    )
    strategy_params: dict[str, bool | int | float | str] = Field(default_factory=dict)
    trade_size: Decimal = Field(default=Decimal("100000"))
    account_currency: str = Field(default="USD")

    @model_validator(mode="after")
    def _normalize_instruments(self) -> "LiveTradingConfig":
        """Ensure both ``instrument`` and ``instruments`` are consistent."""
        if not self.instruments and self.instrument:
            self.instruments = [self.instrument]
        if self.instruments and not self.instrument:
            self.instrument = self.instruments[0]
        if not self.instruments:
            msg = "At least one instrument is required"
            raise ValueError(msg)
        return self


class TradingEvent(BaseModel):
    """A timestamped event from a live trading session."""

    timestamp: datetime
    event_type: str = Field(
        description=(
            "Event type: order_submitted, order_filled, position_opened, "
            "position_closed, risk_breach, circuit_breaker, "
            "connection_lost, connection_restored, info"
        ),
    )
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ConnectionTestResult(BaseModel):
    """Result of an IB connection smoke test."""

    success: bool
    elapsed_seconds: float
    diagnostics: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    instrument_specs: dict[str, str] | None = None
    error: str | None = None
