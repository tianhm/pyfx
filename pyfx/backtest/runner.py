"""Backtest runner wrapping NautilusTrader's BacktestEngine."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from decimal import Decimal

import pandas as pd
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.backtest.models import FillModel, MakerTakerFeeModel
from nautilus_trader.config import BacktestEngineConfig, LoggingConfig
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.data import BarType
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
from nautilus_trader.model.objects import Money
from nautilus_trader.persistence.wranglers import BarDataWrangler
from nautilus_trader.test_kit.providers import TestInstrumentProvider

from pyfx.core.config import settings
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


def _parse_nautilus_money_currency(value: object) -> tuple[float, str]:
    """Parse NautilusTrader money strings, returning (amount, currency).

    Examples:
        '-56.40 USD' -> (-56.40, 'USD')
        '914703.00 JPY' -> (914703.00, 'JPY')
    """
    s = str(value)
    parts = s.split()
    if len(parts) >= 2:  # noqa: PLR2004
        return float(parts[0]), parts[1]
    return (float(parts[0]) if parts else 0.0), "USD"


def _convert_pnl_to_usd(
    pnl: float,
    currency: str,
    close_price: float,
    instrument_str: str,
) -> float:
    """Convert P&L from quote currency to USD if needed.

    For pairs where quote is USD (e.g. EUR/USD, XAU/USD), P&L is already in USD.
    For pairs where quote is not USD (e.g. USD/JPY), divide by the close price.
    """
    if currency == "USD" or pnl == 0.0:
        return pnl

    # Quote currency is not USD — convert using the close price.
    # For USD/JPY: pnl_jpy / close_price_usdjpy = pnl_usd
    if close_price <= 0.0:
        return pnl  # can't convert, return raw value

    return pnl / close_price


_AGGREGATION_FREQ: dict[str, str] = {
    "SECOND": "s",
    "MINUTE": "min",
    "HOUR": "h",
    "DAY": "D",
}


def _resample_bars(bars_df: pd.DataFrame, bar_type_str: str) -> pd.DataFrame:
    """Resample OHLCV bars to a higher timeframe.

    Args:
        bars_df: Source DataFrame with OHLCV columns and DatetimeIndex.
        bar_type_str: Bar type spec like ``"60-MINUTE-LAST-EXTERNAL"``.

    Returns:
        Resampled DataFrame with the same column structure.
    """
    parts = bar_type_str.split("-")
    step = int(parts[0])
    aggregation = parts[1]
    suffix = _AGGREGATION_FREQ.get(aggregation)
    if suffix is None:
        raise ValueError(f"Unsupported aggregation '{aggregation}' in '{bar_type_str}'")
    rule = f"{step}{suffix}"

    agg = {
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
    }
    if "volume" in bars_df.columns:
        agg["volume"] = "sum"

    resampled: pd.DataFrame = bars_df.resample(rule).agg(agg).dropna()  # type: ignore[arg-type]
    return resampled


# Instruments that need non-FX precision and lot sizing.
# default_fx_ccy uses 5-decimal for non-JPY — wrong for commodities.
# Each override: (price_precision, tick_scheme, lot_size_str, min_qty_str)
#
# Gold: 2 decimal, 1 oz per unit, min 1 oz.  trade_size=100 = 100 oz.
# Oil:  2 decimal, 1 bbl per unit, min 1 bbl. trade_size=1000 = 1000 bbl.
_INSTRUMENT_OVERRIDES: dict[str, tuple[int, str, str, str]] = {
    "XAU/USD": (2, "FOREX_3DECIMAL", "1", "1"),
    "OIL/USD": (2, "FOREX_3DECIMAL", "1", "1"),
    "BCO/USD": (2, "FOREX_3DECIMAL", "1", "1"),
    "WTI/USD": (2, "FOREX_3DECIMAL", "1", "1"),
}


def _get_instrument(instrument_str: str, venue: str):
    """Get a NautilusTrader instrument for the given string (e.g. 'EUR/USD').

    For standard FX pairs, uses TestInstrumentProvider.default_fx_ccy().
    For commodities (XAU/USD, OIL/USD), creates a CurrencyPair with correct
    price precision (2 decimals instead of 5) so P&L is realistic.
    """
    from nautilus_trader.model.currencies import Currency as NTCurrency
    from nautilus_trader.model.instruments import CurrencyPair
    from nautilus_trader.model.objects import Money, Price, Quantity

    override = _INSTRUMENT_OVERRIDES.get(instrument_str)
    if override is None:
        return TestInstrumentProvider.default_fx_ccy(instrument_str, Venue(venue))

    price_precision, tick_scheme, lot_size_str, min_qty_str = override
    v = Venue(venue)
    instrument_id = InstrumentId(Symbol(instrument_str), v)
    base = instrument_str[:3]
    quote = instrument_str[-3:]

    return CurrencyPair(
        instrument_id=instrument_id,
        raw_symbol=Symbol(instrument_str),
        base_currency=NTCurrency.from_str(base),
        quote_currency=NTCurrency.from_str(quote),
        price_precision=price_precision,
        size_precision=0,
        price_increment=Price(1 / 10**price_precision, price_precision),
        size_increment=Quantity.from_int(1),
        lot_size=Quantity.from_str(lot_size_str),
        max_quantity=Quantity.from_str("1e7"),
        min_quantity=Quantity.from_str(min_qty_str),
        max_price=None,
        min_price=None,
        max_notional=Money(50_000_000.00, USD),
        min_notional=Money(1_000.00, USD),
        margin_init=Decimal("0.03"),
        margin_maint=Decimal("0.03"),
        maker_fee=Decimal("0.00002"),
        taker_fee=Decimal("0.00002"),
        tick_scheme_name=tick_scheme,
        ts_event=0,
        ts_init=0,
    )


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
        fee_model=MakerTakerFeeModel(),
        fill_model=FillModel(
            prob_fill_on_limit=1.0,
            prob_slippage=0.5,
            random_seed=42,
        ),
    )

    engine.add_instrument(instrument)

    # Add base bars (unsorted); extra timeframes follow
    has_extra = len(config.extra_bar_types) > 0
    engine.add_data(bars, sort=not has_extra)

    extra_bar_type_objects: tuple[BarType, ...] = ()
    if has_extra:
        extra_bt_list: list[BarType] = []
        for extra_bt_str in config.extra_bar_types:
            extra_bt = BarType.from_str(f"{instrument.id}-{extra_bt_str}")
            resampled_df = _resample_bars(bars_df, extra_bt_str)
            extra_wrangler = BarDataWrangler(extra_bt, instrument)
            extra_bars = extra_wrangler.process(resampled_df)
            engine.add_data(extra_bars, sort=False)
            extra_bt_list.append(extra_bt)
        extra_bar_type_objects = tuple(extra_bt_list)
        engine.sort_data()

    strategy_cls = get_strategy(config.strategy, settings.strategies_dir)
    config_cls = _find_config_class(strategy_cls)

    # strategy_params may contain trade_size from -p flag; let it override
    params = dict(config.strategy_params)
    trade_size = Decimal(str(params.pop("trade_size", config.trade_size)))

    strategy_config = config_cls(
        instrument_id=instrument.id,
        bar_type=bar_type,
        extra_bar_types=extra_bar_type_objects,
        trade_size=trade_size,
        **params,
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
        # Convert P&L to USD for non-USD-quote pairs (e.g. USD/JPY reports in JPY)
        pnl_usd_values: list[float] = []
        for _, row in positions_report.iterrows():
            raw_pnl, currency = _parse_nautilus_money_currency(row["realized_pnl"])
            close_px = _parse_nautilus_money(row.get("avg_px_close", "0"))
            pnl_usd = _convert_pnl_to_usd(
                raw_pnl, currency, close_px, config.instrument,
            )
            pnl_usd_values.append(pnl_usd)

        pnl_series = pd.Series(pnl_usd_values)
        total_pnl = float(pnl_series.sum())
        wins = pnl_series[pnl_series > 0]
        losses = pnl_series[pnl_series < 0]
        win_rate = float(len(wins) / num_trades)
        total_return_pct = (total_pnl / config.balance) * 100
        avg_trade_pnl = float(pnl_series.mean())
        avg_win = float(wins.mean()) if len(wins) > 0 else 0.0
        avg_loss = float(losses.mean()) if len(losses) > 0 else 0.0

        gross_wins = float(wins.sum()) if len(wins) > 0 else 0.0
        gross_losses = abs(float(losses.sum())) if len(losses) > 0 else 0.0
        if gross_losses > 0:
            profit_factor = gross_wins / gross_losses

        # Extract individual trades
        trade_idx = 0
        for _, row in positions_report.iterrows():
            pnl = pnl_usd_values[trade_idx]
            trade_idx += 1
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
