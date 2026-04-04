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
from pyfx.core.instruments import get_instrument_spec
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


def _parse_nautilus_money(value) -> tuple[float, str]:
    """Parse NautilusTrader money strings like '-56.40 USD' to (amount, currency).

    Returns:
        Tuple of (numeric value, currency code).  Currency defaults to "USD"
        when the string has no currency suffix.
    """
    s = str(value)
    parts = s.split()
    if len(parts) >= 2:  # noqa: PLR2004
        return float(parts[0]), parts[1]
    if parts:
        return float(parts[0]), "USD"
    return 0.0, "USD"


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


def _get_instrument(instrument_str: str, venue: str):
    """Get a NautilusTrader instrument for the given string (e.g. 'EUR/USD').

    Uses the instrument registry to decide whether to use the built-in
    ``TestInstrumentProvider.default_fx_ccy()`` (for standard FX pairs) or
    to construct a custom ``CurrencyPair`` (for commodities and other
    non-standard instruments).
    """
    from nautilus_trader.model.currencies import Currency as NTCurrency
    from nautilus_trader.model.instruments import CurrencyPair
    from nautilus_trader.model.objects import Money, Price, Quantity

    spec = get_instrument_spec(instrument_str)
    if spec.is_fx:
        return TestInstrumentProvider.default_fx_ccy(instrument_str, Venue(venue))

    v = Venue(venue)
    instrument_id = InstrumentId(Symbol(instrument_str), v)
    base = instrument_str[:3]
    quote = instrument_str[-3:]

    return CurrencyPair(
        instrument_id=instrument_id,
        raw_symbol=Symbol(instrument_str),
        base_currency=NTCurrency.from_str(base),
        quote_currency=NTCurrency.from_str(quote),
        price_precision=spec.price_precision,
        size_precision=0,
        price_increment=Price(1 / 10**spec.price_precision, spec.price_precision),
        size_increment=Quantity.from_int(1),
        lot_size=Quantity.from_str(spec.lot_size),
        max_quantity=Quantity.from_str("1e7"),
        min_quantity=Quantity.from_str(spec.min_quantity),
        max_price=None,
        min_price=None,
        max_notional=Money(50_000_000.00, USD),
        min_notional=Money(1_000.00, USD),
        margin_init=Decimal("0.03"),
        margin_maint=Decimal("0.03"),
        maker_fee=Decimal("0.00002"),
        taker_fee=Decimal("0.00002"),
        tick_scheme_name=spec.tick_scheme,
        ts_event=0,
        ts_init=0,
    )


def _add_quote_ticks(
    engine: BacktestEngine,
    instrument,
    bars_df: pd.DataFrame,
    instrument_str: str,
) -> None:
    """Add synthetic quote ticks for exchange rate conversion.

    When the instrument's quote currency differs from the account currency
    (USD), NautilusTrader needs quote tick data to calculate exchange rates
    for P&L conversion.  This derives ticks from bar close prices.
    """
    import numpy as np
    from nautilus_trader.persistence.wranglers import QuoteTickDataWrangler

    spec = get_instrument_spec(instrument_str)
    if not spec.needs_quote_conversion:
        return

    n = len(bars_df)
    half_spread = spec.spread
    quote_df = pd.DataFrame(
        {
            "bid_price": bars_df["close"] - half_spread,
            "ask_price": bars_df["close"] + half_spread,
            "bid_size": np.full(n, 1_000_000.0),
            "ask_size": np.full(n, 1_000_000.0),
        },
        index=bars_df.index,
    )

    wrangler = QuoteTickDataWrangler(instrument)
    quotes = wrangler.process(quote_df)
    engine.add_data(quotes, sort=False)


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
            random_seed=config.random_seed if config.random_seed is not None else int.from_bytes(
                __import__("os").urandom(4), "big",
            ),
        ),
    )

    engine.add_instrument(instrument)

    # Add quote ticks for exchange rate conversion (non-USD-quoted pairs)
    _add_quote_ticks(engine, instrument, bars_df, config.instrument)

    # Add base bars (unsorted); extra timeframes and quote ticks may follow
    engine.add_data(bars, sort=False)

    extra_bar_type_objects: tuple[BarType, ...] = ()
    if len(config.extra_bar_types) > 0:
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


def _convert_pnl_to_usd(
    pnl: float,
    currency: str,
    close_price: float,
) -> float:
    """Convert a P&L value from quote currency to USD.

    For USD-denominated P&L, returns the value unchanged.  For other
    currencies (e.g. JPY), divides by the instrument close price which
    represents the exchange rate (e.g. 150 JPY per USD).
    """
    if currency == "USD" or close_price == 0.0:
        return pnl
    return pnl / close_price


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
        # Extract individual trades with currency-aware P&L conversion
        usd_pnl_values: list[float] = []
        for _, row in positions_report.iterrows():
            raw_pnl, pnl_currency = _parse_nautilus_money(row["realized_pnl"])
            raw_return, _ = _parse_nautilus_money(row.get("realized_return", "0"))
            close_price, _ = _parse_nautilus_money(row.get("avg_px_close", "0"))

            # Convert P&L to account currency (USD)
            pnl_usd = _convert_pnl_to_usd(raw_pnl, pnl_currency, close_price)
            usd_pnl_values.append(pnl_usd)

            opened_at = _to_utc_datetime(row.get("ts_opened", 0))
            closed_at = _to_utc_datetime(row.get("ts_closed", 0))

            duration_ns = row.get("duration_ns", 0)
            duration_s = float(duration_ns) / 1e9 if duration_ns else 0.0

            return_pct = raw_return * 100 if abs(raw_return) < 1 else raw_return

            trades.append(TradeRecord(
                instrument=str(row.get("instrument_id", config.instrument)),
                side=str(row.get("entry", row.get("side", ""))),
                quantity=float(str(row.get("quantity", "0")).split()[0]),
                open_price=_parse_nautilus_money(row.get("avg_px_open", "0"))[0],
                close_price=close_price,
                realized_pnl=pnl_usd,
                pnl_currency=pnl_currency,
                realized_return_pct=return_pct,
                opened_at=opened_at,
                closed_at=closed_at,
                duration_seconds=duration_s,
            ))

        # Compute aggregate stats from USD-converted P&L
        import numpy as np

        pnl_arr = np.array(usd_pnl_values)
        total_pnl = float(pnl_arr.sum())
        win_mask = pnl_arr > 0
        loss_mask = pnl_arr < 0
        win_rate = float(win_mask.sum() / num_trades)
        total_return_pct = (total_pnl / config.balance) * 100
        avg_trade_pnl = float(pnl_arr.mean())
        avg_win = float(pnl_arr[win_mask].mean()) if win_mask.any() else 0.0
        avg_loss = float(pnl_arr[loss_mask].mean()) if loss_mask.any() else 0.0

        gross_wins = float(pnl_arr[win_mask].sum()) if win_mask.any() else 0.0
        gross_losses = abs(float(pnl_arr[loss_mask].sum())) if loss_mask.any() else 0.0
        if gross_losses > 0:
            profit_factor = gross_wins / gross_losses

        # Build equity curve from trade P&L (more reliable than account report
        # for cross-currency pairs where the balance column may not reflect P&L)
        sorted_trades = sorted(trades, key=lambda t: t.closed_at)
        running_balance = config.balance
        balances_list: list[float] = [running_balance]
        for t in sorted_trades:
            running_balance += t.realized_pnl
            balances_list.append(running_balance)
            equity_curve.append(
                EquityPoint(timestamp=t.closed_at, balance=running_balance)
            )

        balance_arr = np.array(balances_list)
        peak = np.maximum.accumulate(balance_arr)
        drawdown = (balance_arr - peak) / peak * 100
        max_drawdown_pct = float(drawdown.min())

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
