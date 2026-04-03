"""Backtest runner wrapping NautilusTrader's BacktestEngine."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from decimal import Decimal

import pandas as pd
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.config import BacktestEngineConfig, LoggingConfig
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.data import BarType
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.objects import Money
from nautilus_trader.persistence.wranglers import BarDataWrangler
from nautilus_trader.test_kit.providers import TestInstrumentProvider

from pyfx.core.types import BacktestConfig, BacktestResult, EquityPoint, TradeRecord
from pyfx.strategies.loader import get_strategy


def _to_utc_datetime(value) -> datetime:
    """Convert various timestamp formats to a UTC datetime."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value
    if isinstance(value, pd.Timestamp):
        if value.tzinfo is None:
            return value.tz_localize("UTC").to_pydatetime()
        return value.to_pydatetime()
    if isinstance(value, (int, float)):
        if value > 1e15:  # nanoseconds
            return datetime.fromtimestamp(value / 1e9, tz=UTC)
        elif value > 1e12:  # microseconds
            return datetime.fromtimestamp(value / 1e6, tz=UTC)
        else:  # seconds
            return datetime.fromtimestamp(value, tz=UTC)
    # String fallback
    return pd.Timestamp(str(value)).tz_localize("UTC").to_pydatetime()


def _parse_nautilus_money(value) -> float:
    """Parse NautilusTrader money strings like '-56.40 USD' to float."""
    s = str(value)
    parts = s.split()
    return float(parts[0]) if parts else 0.0


def _get_instrument(instrument_str: str, venue: str):
    """Get a NautilusTrader instrument for the given string (e.g. 'EUR/USD')."""
    return TestInstrumentProvider.default_fx_ccy(instrument_str, Venue(venue))


def run_backtest(
    config: BacktestConfig,
    bars_df: pd.DataFrame,
    log_level: str = "ERROR",
) -> BacktestResult:
    """Run a backtest with the given config and bar data.

    Args:
        config: Backtest configuration.
        bars_df: DataFrame with columns: open, high, low, close, volume (optional).
                 Index must be a DatetimeIndex in UTC.
        log_level: NautilusTrader log level.

    Returns:
        BacktestResult with full metrics, trades, and equity curve.
    """
    t0 = time.monotonic()

    venue = Venue(config.venue)
    instrument = _get_instrument(config.instrument, config.venue)

    bar_type = BarType.from_str(f"{instrument.id}-{config.bar_type}")

    wrangler = BarDataWrangler(bar_type, instrument)
    bars = wrangler.process(bars_df)

    engine = BacktestEngine(
        config=BacktestEngineConfig(
            logging=LoggingConfig(log_level=log_level),
        ),
    )

    engine.add_venue(
        venue=venue,
        oms_type=OmsType.NETTING,
        account_type=AccountType.MARGIN,
        starting_balances=[Money(config.balance, USD)],
        base_currency=USD,
        default_leverage=Decimal(str(config.leverage)),
    )

    engine.add_instrument(instrument)
    engine.add_data(bars)

    strategy_cls = get_strategy(config.strategy)
    config_cls = _find_config_class(strategy_cls)
    strategy_config = config_cls(
        instrument_id=instrument.id,
        bar_type=bar_type,
        trade_size=config.trade_size,
        **config.strategy_params,
    )

    strategy = strategy_cls(strategy_config)
    engine.add_strategy(strategy)

    engine.run()

    result = _extract_results(engine, config, venue)
    result.duration_seconds = time.monotonic() - t0

    engine.dispose()

    return result


def _find_config_class(strategy_cls: type) -> type:
    """Find the StrategyConfig class associated with a strategy."""
    import inspect

    sig = inspect.signature(strategy_cls.__init__)
    for param in sig.parameters.values():
        if param.name == "config" and param.annotation != inspect.Parameter.empty:
            ann = param.annotation
            if isinstance(ann, str):
                import importlib
                module = importlib.import_module(strategy_cls.__module__)
                return getattr(module, ann)
            return ann

    import importlib
    module = importlib.import_module(strategy_cls.__module__)
    for attr_name in dir(module):
        attr = getattr(module, attr_name)
        if (
            isinstance(attr, type)
            and attr_name.endswith("Config")
            and attr_name != "StrategyConfig"
        ):
            return attr

    raise ValueError(f"Could not find config class for strategy {strategy_cls.__name__}")


def _extract_results(
    engine: BacktestEngine,
    config: BacktestConfig,
    venue: Venue,
) -> BacktestResult:
    """Extract full results from a completed backtest engine."""
    positions_report: pd.DataFrame = engine.trader.generate_positions_report()

    num_trades = len(positions_report)
    total_pnl = 0.0
    win_rate = 0.0
    total_return_pct = 0.0
    max_drawdown_pct = 0.0
    avg_trade_pnl = 0.0
    avg_win = 0.0
    avg_loss = 0.0
    profit_factor = None
    trades: list[TradeRecord] = []
    equity_curve: list[EquityPoint] = []

    if num_trades > 0:
        pnl_values = positions_report["realized_pnl"].apply(_parse_nautilus_money)
        total_pnl = float(pnl_values.sum())
        wins = pnl_values[pnl_values > 0]
        losses = pnl_values[pnl_values < 0]
        win_rate = float(len(wins) / num_trades)
        total_return_pct = (total_pnl / config.balance) * 100
        avg_trade_pnl = float(pnl_values.mean())
        avg_win = float(wins.mean()) if len(wins) > 0 else 0.0
        avg_loss = float(losses.mean()) if len(losses) > 0 else 0.0

        gross_wins = float(wins.sum()) if len(wins) > 0 else 0.0
        gross_losses = abs(float(losses.sum())) if len(losses) > 0 else 0.0
        if gross_losses > 0:
            profit_factor = gross_wins / gross_losses

        # Extract individual trades
        for _, row in positions_report.iterrows():
            pnl = _parse_nautilus_money(row["realized_pnl"])
            return_pct = _parse_nautilus_money(row.get("realized_return", "0"))

            opened_at = _to_utc_datetime(row.get("ts_opened", 0))
            closed_at = _to_utc_datetime(row.get("ts_closed", 0))

            duration_ns = row.get("duration_ns", 0)
            duration_s = float(duration_ns) / 1e9 if duration_ns else 0.0

            trades.append(TradeRecord(
                instrument=str(row.get("instrument_id", config.instrument)),
                side=str(row.get("side", "")),
                quantity=float(str(row.get("quantity", "0")).split()[0]),
                open_price=_parse_nautilus_money(row.get("avg_px_open", "0")),
                close_price=_parse_nautilus_money(row.get("avg_px_close", "0")),
                realized_pnl=pnl,
                realized_return_pct=return_pct * 100 if abs(return_pct) < 1 else return_pct,
                opened_at=opened_at,
                closed_at=closed_at,
                duration_seconds=duration_s,
            ))

        # Extract equity curve from account report
        account_report: pd.DataFrame = engine.trader.generate_account_report(venue)
        # The report uses "total" for account balance (not "balance")
        bal_col = "total" if "total" in account_report.columns else "balance"
        if not account_report.empty and bal_col in account_report.columns:
            balances = account_report[bal_col].apply(
                lambda x: float(str(x).replace(",", "")) if pd.notna(x) else 0.0
            )
            peak = balances.cummax()
            drawdown = (balances - peak) / peak * 100
            max_drawdown_pct = float(drawdown.min())

            for ts, bal in zip(account_report.index, balances):
                dt = _to_utc_datetime(ts)
                equity_curve.append(EquityPoint(timestamp=dt, balance=float(bal)))

    # Add start point to equity curve
    if not equity_curve:
        start_ts = config.start
        if start_ts.tzinfo is None:
            start_ts = start_ts.replace(tzinfo=UTC)
        equity_curve.append(EquityPoint(timestamp=start_ts, balance=config.balance))

    return BacktestResult(
        config=config,
        total_pnl=total_pnl,
        total_return_pct=total_return_pct,
        num_trades=num_trades,
        win_rate=win_rate,
        max_drawdown_pct=max_drawdown_pct,
        avg_trade_pnl=avg_trade_pnl,
        avg_win=avg_win,
        avg_loss=avg_loss,
        profit_factor=profit_factor,
        trades=trades,
        equity_curve=equity_curve,
    )
