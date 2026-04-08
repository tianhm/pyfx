"""CLI entry point for pyfx."""

from __future__ import annotations

import os
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    import pandas as pd

    from pyfx.core.types import BacktestResult

from pyfx.core.config import settings

_DJANGO_SETTINGS_MODULE = "pyfx.web.pyfx_web.settings"


def _setup_django() -> None:
    """Configure and initialise Django (idempotent)."""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", _DJANGO_SETTINGS_MODULE)

    import django

    django.setup()


@click.group()
@click.version_option(package_name="pyfx")
def main() -> None:
    """pyfx - Backtesting and live trading tool."""


@main.command()
@click.option("--strategy", "-s", required=True, help="Strategy name")
@click.option("--instrument", "-i", default="EUR/USD", help="Instrument (e.g. EUR/USD)")
@click.option("--start", required=True, type=click.DateTime(), help="Start date")
@click.option("--end", required=True, type=click.DateTime(), help="End date")
@click.option("--bar-type", default="1-MINUTE-LAST-EXTERNAL", help="Bar type spec")
@click.option(
    "--extra-bar-type", multiple=True,
    help="Extra bar types for multi-timeframe (repeatable)",
)
@click.option("--trade-size", default="100000", help="Trade size")
@click.option("--balance", default=100_000.0, help="Starting balance (USD)")
@click.option("--leverage", default=50.0, help="Leverage ratio")
@click.option("--data-file", type=click.Path(exists=True, path_type=Path), help="CSV/Parquet data")
@click.option("--log-level", default="ERROR", help="NautilusTrader log level")
@click.option("--save/--no-save", default=False, help="Save results to Django database")
@click.option(
    "--seed", default=42, type=int,
    help="Random seed for slippage model (0 = random each run)",
)
@click.option(
    "--param", "-p", multiple=True,
    help="Strategy param as key=value (e.g. -p fast_period=10)",
)
def backtest(
    strategy: str,
    instrument: str,
    start: datetime,
    end: datetime,
    bar_type: str,
    extra_bar_type: tuple[str, ...],
    trade_size: str,
    balance: float,
    leverage: float,
    data_file: Path | None,
    log_level: str,
    save: bool,
    seed: int,
    param: tuple[str, ...],
) -> None:
    """Run a backtest."""

    from pyfx.backtest.runner import run_backtest
    from pyfx.core.types import BacktestConfig

    strategy_params = _parse_params(param)

    config = BacktestConfig(
        strategy=strategy,
        instrument=instrument,
        start=start,
        end=end,
        bar_type=bar_type,
        extra_bar_types=list(extra_bar_type),
        trade_size=Decimal(trade_size),
        balance=balance,
        leverage=leverage,
        strategy_params=strategy_params,
        random_seed=seed if seed != 0 else None,
    )

    bars_df = _load_data(data_file, start, end)

    click.echo(f"Running backtest: {strategy} on {instrument}")
    click.echo(f"  Period: {start.date()} to {end.date()}")
    click.echo(f"  Bars: {len(bars_df)}")

    result = run_backtest(config, bars_df, log_level=log_level)

    click.echo(f"\n--- Results ({result.duration_seconds:.1f}s) ---")
    click.echo(f"  Total P&L:      ${result.total_pnl:,.2f}")
    click.echo(f"  Return:          {result.total_return_pct:+.2f}%")
    click.echo(f"  Trades:          {result.num_trades}")
    click.echo(f"  Win rate:        {result.win_rate:.1%}")
    click.echo(f"  Avg trade:       ${result.avg_trade_pnl:,.2f}")
    click.echo(f"  Avg win:         ${result.avg_win:,.2f}")
    click.echo(f"  Avg loss:        ${result.avg_loss:,.2f}")
    if result.profit_factor is not None:
        click.echo(f"  Profit factor:   {result.profit_factor:.2f}")
    click.echo(f"  Max drawdown:    {result.max_drawdown_pct:.2f}%")

    if save:
        _save_to_django(result)
        click.echo("\n  Results saved to database.")


@main.command("strategies")
def list_strategies() -> None:
    """List available strategies."""
    from pyfx.strategies.loader import discover_strategies

    strategies = discover_strategies(settings.strategies_dir)
    if not strategies:
        click.echo("No strategies found.")
        return

    click.echo("Available strategies:")
    for name, cls in sorted(strategies.items()):
        doc = (cls.__doc__ or "").strip().split("\n")[0]
        click.echo(f"  {name:30s} {doc}")


@main.command("generate-sample-data")
@click.option("--instrument", "-i", default="EUR/USD", help="Instrument name")
@click.option("--days", default=365, help="Number of days of M1 data")
@click.option("--output", "-o", type=click.Path(path_type=Path), default=None, help="Output path")
def generate_sample_data(instrument: str, days: int, output: Path | None) -> None:
    """Generate synthetic sample data for testing."""
    import numpy as np
    import pandas as pd

    from pyfx.core.instruments import get_instrument_spec

    spec = get_instrument_spec(instrument)

    if output is None:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        safe_name = instrument.replace("/", "")
        output = settings.data_dir / f"{safe_name}_{days}d_M1.parquet"

    n = days * 24 * 60  # 1-minute bars
    rng = np.random.default_rng(42)

    # Random walk with mean reversion using instrument-appropriate scales
    base_price = spec.base_price
    returns = rng.normal(0, spec.volatility, n)
    price = base_price + np.cumsum(returns)
    # Mean revert gently
    reversion_rate = spec.volatility  # proportional to volatility
    for i in range(1, len(price)):
        price[i] += (base_price - price[i]) * reversion_rate

    half_spread = spec.spread
    spread = np.abs(rng.normal(half_spread, half_spread * 0.4, n))

    bars_df = pd.DataFrame(
        {
            "open": price,
            "high": price + spread + np.abs(rng.normal(0, spec.volatility, n)),
            "low": price - spread - np.abs(rng.normal(0, spec.volatility, n)),
            "close": price + rng.normal(0, spec.volatility * 0.5, n),
            "volume": rng.integers(500_000, 2_000_000, n).astype(float),
        },
        index=pd.date_range("2023-01-01", periods=n, freq="1min", tz="UTC"),
    )
    bars_df["high"] = bars_df[["open", "high", "close"]].max(axis=1)
    bars_df["low"] = bars_df[["open", "low", "close"]].min(axis=1)

    bars_df.to_parquet(output)
    click.echo(f"Generated {n:,} bars ({days} days of M1 data)")
    click.echo(f"Saved to: {output}")


@main.command("ingest")
@click.option(
    "--input", "-i", "input_path",
    required=True, type=click.Path(exists=True, path_type=Path),
    help="Path to Dukascopy CSV file",
)
@click.option(
    "--output", "-o", "output_path",
    type=click.Path(path_type=Path), default=None,
    help="Output Parquet path (default: same name with .parquet)",
)
def ingest(input_path: Path, output_path: Path | None) -> None:
    """Ingest a Dukascopy CSV file into Parquet format."""
    from pyfx.data.dukascopy import ingest_to_parquet, read_dukascopy_csv

    click.echo(f"Reading: {input_path}")
    df = read_dukascopy_csv(input_path)
    click.echo(f"  Rows:  {len(df):,}")
    click.echo(f"  Range: {df.index[0]} to {df.index[-1]}")

    out = ingest_to_parquet(input_path, output_path)
    click.echo(f"Saved:   {out}")


@main.command("web")
@click.option("--host", default="127.0.0.1", help="Host to bind")
@click.option("--port", default=8000, help="Port to bind")
@click.option("--no-reload", is_flag=True, default=False, help="Disable auto-reload")
def web(host: str, port: int, no_reload: bool) -> None:
    """Start the Django dashboard."""
    _ensure_migrated()

    from django.core.management import execute_from_command_line

    # Auto-scan data directory for unregistered Parquet files
    from pyfx.data.scanner import scan_data_directory

    registered, _ = scan_data_directory(quiet=True)
    if registered:
        click.echo(f"Registered {registered} new dataset(s) from data directory.")

    click.echo(f"Starting pyfx dashboard at http://{host}:{port}/")
    argv = ["pyfx", "runserver", f"{host}:{port}"]
    if no_reload:
        argv.append("--noreload")
    execute_from_command_line(argv)


@main.command("manage", context_settings={"ignore_unknown_options": True})
@click.argument("args", nargs=-1, type=click.UNPROCESSED)
def manage(args: tuple[str, ...]) -> None:
    """Run Django management commands (e.g. pyfx manage migrate)."""
    _setup_django()

    from django.core.management import execute_from_command_line

    execute_from_command_line(["pyfx", *args])


@main.group()
def data() -> None:
    """Manage OHLCV datasets."""


@data.command("list")
def data_list() -> None:
    """List available datasets."""
    _setup_django()

    from pyfx.web.dashboard.models import Dataset

    datasets = Dataset.objects.all()
    if not datasets:
        click.echo("No datasets registered.")
        click.echo("  Run `pyfx data scan` to detect existing Parquet files.")
        return

    # Header
    click.echo(
        f"{'Instrument':12s} {'TF':4s} {'Start':12s} {'End':12s} "
        f"{'Rows':>10s} {'Size':>8s} {'Source':10s} {'Status':10s}"
    )
    click.echo("-" * 82)

    for ds in datasets:
        click.echo(
            f"{ds.instrument:12s} {ds.timeframe:4s} {ds.start_date!s:12s} "
            f"{ds.end_date!s:12s} {ds.row_count:>10,} {ds.display_size:>8s} "
            f"{ds.source:10s} {ds.status:10s}"
        )


@data.command("scan")
def data_scan() -> None:
    """Scan data directory and register untracked Parquet files."""
    _ensure_migrated()

    from pyfx.data.scanner import scan_data_directory

    registered, already_tracked = scan_data_directory()
    click.echo(f"Registered {registered} new dataset(s), {already_tracked} already tracked.")


@main.group()
def live() -> None:
    """Paper / live trading with Interactive Brokers."""


@live.command("start")
@click.option("--strategy", "-s", required=True, help="Strategy name")
@click.option(
    "--instrument", "-i", multiple=True, default=("XAU/USD",),
    help="Instrument (repeatable, e.g. -i XAU/USD -i EUR/USD)",
)
@click.option("--bar-type", default="1-MINUTE-LAST-EXTERNAL", help="Bar type spec")
@click.option(
    "--extra-bar-type", multiple=True,
    help="Extra bar types for multi-timeframe (repeatable)",
)
@click.option("--trade-size", default="100000", help="Trade size")
@click.option(
    "--param", "-p", multiple=True,
    help="Strategy param as key=value (e.g. -p entry_mode=trend_follow)",
)
@click.option(
    "--confirm-live", is_flag=True, default=False,
    help="Required when connecting to a LIVE (non-paper) IB account",
)
def live_start(
    strategy: str,
    instrument: tuple[str, ...],
    bar_type: str,
    extra_bar_type: tuple[str, ...],
    trade_size: str,
    param: tuple[str, ...],
    confirm_live: bool,
) -> None:
    """Start a paper trading session (Ctrl+C to stop)."""
    from pyfx.core.types import LiveTradingConfig

    # Validate IB config
    warnings = settings.validate_ib_config()
    for w in warnings:
        click.echo(f"  Warning: {w}", err=True)

    if settings.ib_trading_mode == "live" and not confirm_live:
        click.echo("Error: trading_mode is 'live'. Use --confirm-live to proceed.", err=True)
        raise SystemExit(1)

    strategy_params = _parse_params(param)

    config = LiveTradingConfig(
        strategy=strategy,
        instruments=list(instrument),
        bar_type=bar_type,
        extra_bar_types=list(extra_bar_type),
        strategy_params=strategy_params,
        trade_size=Decimal(trade_size),
        account_currency=settings.account_currency,
    )

    click.echo(f"Starting paper trading: {strategy} on {', '.join(instrument)}")
    click.echo(f"  IB Gateway: {settings.ib_host}:{settings.ib_port}")
    click.echo(f"  Account: {settings.ib_account_id or '(not set)'}")
    click.echo(f"  Mode: {settings.ib_trading_mode}")
    click.echo("  Press Ctrl+C to stop gracefully.\n")

    from pyfx.live.runner import start_live_trading

    start_live_trading(config, settings)


@live.command("stop")
def live_stop() -> None:
    """Mark the most recent running session as stopped."""
    _setup_django()
    from pyfx.web.dashboard.models import PaperTradingSession

    session = (
        PaperTradingSession.objects
        .filter(status=PaperTradingSession.STATUS_RUNNING)
        .order_by("-started_at")
        .first()
    )
    if session is None:
        click.echo("No running paper trading sessions found.")
        return

    from pyfx.web.dashboard.services import stop_paper_session

    stop_paper_session(session.pk)
    click.echo(f"Session #{session.pk} marked as stopped.")


@live.command("status")
@click.option("--session-id", type=int, default=None, help="Session ID (default: most recent)")
def live_status(session_id: int | None) -> None:
    """Show current paper trading session status."""
    from pyfx.live.runner import get_session_status

    status = get_session_status(session_id)
    if "error" in status:
        click.echo(str(status["error"]))
        return

    click.echo(f"Session #{status['session_id']}  [{status['status']}]")
    click.echo(f"  Strategy:    {status['strategy']}")
    click.echo(f"  Instrument:  {status['instrument']}")
    click.echo(f"  Started:     {status['started_at']}")
    if status["stopped_at"]:
        click.echo(f"  Stopped:     {status['stopped_at']}")

    pnl = status.get("total_pnl")
    if pnl is not None:
        click.echo(f"\n  Total P&L:   ${pnl:+,.2f}")
    ret = status.get("total_return_pct")
    if ret is not None:
        click.echo(f"  Return:      {ret:+.2f}%")
    click.echo(f"  Trades:      {status.get('num_trades', 0)}")
    wr = status.get("win_rate")
    if wr is not None:
        click.echo(f"  Win rate:    {wr:.1%}")
    pf = status.get("profit_factor")
    if pf is not None:
        click.echo(f"  PF:          {pf:.2f}")
    dd = status.get("max_drawdown_pct")
    if dd is not None:
        click.echo(f"  Max DD:      {dd:.2f}%")

    open_trades = status.get("open_trades", [])
    if open_trades:
        click.echo(f"\n  Open positions ({len(open_trades)}):")
        for t in open_trades:
            click.echo(
                f"    {t['side']} {t['quantity']} {t['instrument']} "
                f"@ {t['open_price']}",
            )

    events = status.get("recent_events", [])
    if events:
        click.echo("\n  Recent events:")
        for ev in events[:5]:
            click.echo(f"    [{ev['event_type']}] {ev['message']}")


@live.command("history")
@click.option("--last", "last_n", type=int, default=None, help="Show last N items")
@click.option(
    "--since", type=click.DateTime(), default=None,
    help="Show items since this date/time",
)
def live_history(last_n: int | None, since: datetime | None) -> None:
    """Review recent trades and events (morning review)."""
    from pyfx.live.runner import get_session_history

    history = get_session_history(last_n=last_n, since=since)
    data = history[0] if history else {"events": [], "trades": []}

    trades = data.get("trades", [])
    if trades:
        click.echo(f"Trades ({len(trades)}):")
        for t in trades:
            pnl_str = (
                f"${t['realized_pnl']:+.2f}" if t.get("realized_pnl") is not None else "open"
            )
            click.echo(
                f"  {t['opened_at']}  {t['side']:4s} {t['instrument']}  {pnl_str}",
            )
    else:
        click.echo("No trades found.")

    events = data.get("events", [])
    if events:
        click.echo(f"\nEvents ({len(events)}):")
        for ev in events:
            click.echo(f"  {ev['timestamp']}  [{ev['event_type']}] {ev['message']}")
    else:
        click.echo("\nNo events found.")


@live.command("compare")
@click.option("--session", "session_id", type=int, default=None, help="Paper session ID")
@click.option("--backtest", "backtest_id", type=int, default=None, help="Backtest run ID")
@click.option(
    "--format", "fmt", type=click.Choice(["table", "json"]),
    default="table", help="Output format",
)
def live_compare(
    session_id: int | None,
    backtest_id: int | None,
    fmt: str,
) -> None:
    """Compare a paper trading session against a backtest."""
    _setup_django()

    from pyfx.analysis.comparison import compare_sessions

    report = compare_sessions(
        session_id=session_id,
        backtest_id=backtest_id,
    )

    if fmt == "json":
        import json

        click.echo(json.dumps(report.model_dump(mode="json"), indent=2))
        return

    # Table format
    click.echo(
        f"Paper Session #{report.paper_session_id} vs "
        f"Backtest #{report.backtest_run_id}",
    )
    click.echo("=" * 60)
    click.echo(f"{'':20s} {'Paper':>12s} {'Backtest':>12s} {'Delta':>12s}")
    click.echo("-" * 60)
    click.echo(
        f"{'Total P&L':20s} "
        f"${report.total_pnl_paper:>10,.2f} "
        f"${report.total_pnl_backtest:>10,.2f} "
        f"${report.pnl_difference:>+10,.2f}",
    )
    trades_delta = report.trades_paper - report.trades_backtest
    click.echo(
        f"{'Trades':20s} "
        f"{report.trades_paper:>12d} "
        f"{report.trades_backtest:>12d} "
        f"{trades_delta:>+12d}",
    )
    click.echo(
        f"{'Win Rate':20s} "
        f"{report.win_rate_paper:>11.1%} "
        f"{report.win_rate_backtest:>11.1%} "
        f"{report.win_rate_paper - report.win_rate_backtest:>+11.1%}",
    )
    if report.profit_factor_paper is not None and report.profit_factor_backtest is not None:
        click.echo(
            f"{'Profit Factor':20s} "
            f"{report.profit_factor_paper:>12.2f} "
            f"{report.profit_factor_backtest:>12.2f} "
            f"{report.profit_factor_paper - report.profit_factor_backtest:>+12.2f}",
        )
    click.echo(
        f"\nMatched trades:      {len(report.matched)}",
    )
    click.echo(f"Paper-only trades:   {len(report.paper_only)}")
    click.echo(f"Backtest-only trades: {len(report.backtest_only)}")
    if report.avg_slippage_delta is not None:
        click.echo(f"Avg slippage delta:  {report.avg_slippage_delta:.4f}")

    if report.daily_comparison:
        click.echo(f"\n{'Date':12s} {'Paper':>10s} {'Backtest':>10s} {'Delta':>10s}")
        click.echo("-" * 46)
        for d in report.daily_comparison:
            click.echo(
                f"{str(d.date):12s} "
                f"${d.paper_pnl:>9,.2f} "
                f"${d.backtest_pnl:>9,.2f} "
                f"${d.delta:>+9,.2f}",
            )


@live.command("config")
def live_config() -> None:
    """Show current live trading configuration."""
    click.echo("Live Trading Configuration")
    click.echo("=" * 40)
    click.echo(f"  IB Host:         {settings.ib_host}")
    click.echo(f"  IB Port:         {settings.ib_port}")
    click.echo(f"  IB Account:      {settings.ib_account_id or '(not set)'}")
    click.echo(f"  Trading Mode:    {settings.ib_trading_mode}")
    click.echo(f"  Read-only API:   {settings.ib_read_only_api}")
    click.echo(f"  Gateway Image:   {settings.ib_gateway_image}")
    click.echo(f"  Account Currency: {settings.account_currency}")
    click.echo()
    click.echo("Risk Management")
    click.echo("-" * 40)
    click.echo(f"  Sizing Method:   {settings.risk_sizing_method}")
    click.echo(f"  Position Size %: {settings.risk_position_size_pct}%")
    click.echo(f"  Max Positions:   {settings.risk_max_positions}")
    click.echo(f"  Max Position Sz: {settings.risk_max_position_size}")
    click.echo(f"  Daily Loss Limit: ${settings.risk_daily_loss_limit:,.2f}")
    click.echo(f"  Max Drawdown:    {settings.risk_max_drawdown_pct}%")
    click.echo(f"  Max Notional:    ${settings.risk_max_notional_per_order:,}")

    warnings = settings.validate_ib_config()
    if warnings:
        click.echo("\nWarnings:")
        for w in warnings:
            click.echo(f"  - {w}")


@live.command("test-connection")
@click.option("--instrument", "-i", default="XAU/USD", help="Instrument to test")
@click.option("--timeout", default=30, type=int, help="Timeout in seconds")
def live_test_connection(instrument: str, timeout: int) -> None:
    """Test IB Gateway connection without starting a session."""
    from pyfx.live.connection import validate_ib_config

    click.echo("IB Connection Test")
    click.echo("=" * 40)

    # Quick config validation first
    result = validate_ib_config(settings)
    for d in result.diagnostics:
        click.echo(f"  {d}")
    for w in result.warnings:
        click.echo(f"  Warning: {w}", err=True)

    if not result.success:
        click.echo(f"\nConfig validation failed: {result.error}")
        raise SystemExit(1)

    click.echo("\nConfig OK. To test live connection, ensure Docker is running")
    click.echo(f"and IB Gateway will start on {settings.ib_host}:{settings.ib_port}")


def _parse_params(params: tuple[str, ...]) -> dict[str, bool | int | float | str]:
    """Parse ``key=value`` CLI params, coercing to bool, int, float, or str."""
    from pyfx.core.types import parse_strategy_params

    return parse_strategy_params(params)


def _load_data(
    data_file: Path | None,
    start: datetime,
    end: datetime,
) -> pd.DataFrame:
    """Load bar data from CSV or Parquet, filter to the date range.

    Exits with code 1 if no data file is given or the filtered data is empty.
    """
    if data_file is None:
        click.echo("Error: --data-file is required")
        click.echo("  Generate sample data with: pyfx generate-sample-data")
        raise SystemExit(1)

    from pyfx.data.loader import load_backtest_data

    try:
        return load_backtest_data(data_file, start, end)
    except FileNotFoundError as exc:
        click.echo(f"Error: {exc}")
        raise SystemExit(1) from exc
    except ValueError as exc:
        click.echo(f"Error: {exc}")
        raise SystemExit(1) from exc


_migrated = False


def _ensure_migrated() -> None:
    """Run Django migrations once per process."""
    global _migrated  # noqa: PLW0603
    if _migrated:
        return
    _setup_django()

    from django.core.management import call_command

    call_command("migrate", "--run-syncdb", verbosity=0)
    _migrated = True


def _save_to_django(result: BacktestResult) -> None:
    """Save backtest result to Django database."""
    _ensure_migrated()

    from pyfx.web.dashboard.services import save_backtest_result

    save_backtest_result(result)


if __name__ == "__main__":
    main()
