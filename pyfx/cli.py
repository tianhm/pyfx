"""CLI entry point for pyfx."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, cast

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
    _setup_django()

    from django.core.management import call_command, execute_from_command_line

    # Auto-migrate on first run
    call_command("migrate", "--run-syncdb", verbosity=0)

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
    _setup_django()

    from django.core.management import call_command

    call_command("migrate", "--run-syncdb", verbosity=0)

    from pyfx.data.scanner import scan_data_directory

    registered, already_tracked = scan_data_directory()
    click.echo(f"Registered {registered} new dataset(s), {already_tracked} already tracked.")


def _parse_params(params: tuple[str, ...]) -> dict[str, int | float | str]:
    """Parse ``key=value`` CLI params, coercing to int, float, or str."""
    result: dict[str, int | float | str] = {}
    for p in params:
        key, _, value = p.partition("=")
        try:
            result[key] = int(value)
        except ValueError:
            try:
                result[key] = float(value)
            except ValueError:
                result[key] = value
    return result


def _load_data(
    data_file: Path | None,
    start: datetime,
    end: datetime,
) -> pd.DataFrame:
    """Load bar data from CSV or Parquet, filter to the date range.

    Exits with code 1 if no data file is given or the filtered data is empty.
    """
    import pandas as pd

    if data_file is None:
        click.echo("Error: --data-file is required")
        click.echo("  Generate sample data with: pyfx generate-sample-data")
        raise SystemExit(1)

    if data_file.suffix == ".parquet":
        bars_df = pd.read_parquet(data_file)
    else:
        bars_df = pd.read_csv(data_file, index_col=0, parse_dates=True)

    idx = cast("pd.DatetimeIndex", bars_df.index)
    if idx.tz is None:
        bars_df.index = idx.tz_localize("UTC")

    # Make start/end tz-aware to match the data index
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    if end.tzinfo is None:
        end = end.replace(tzinfo=UTC)

    bars_df = bars_df.loc[start:end]  # type: ignore[misc]

    if bars_df.empty:
        click.echo("Error: no data in the specified date range")
        raise SystemExit(1)

    return bars_df


def _save_to_django(result: BacktestResult) -> None:
    """Save backtest result to Django database."""
    _setup_django()

    from django.core.management import call_command

    call_command("migrate", "--run-syncdb", verbosity=0)

    from pyfx.web.dashboard.models import BacktestRun, EquitySnapshot, Trade

    # Ensure tz-aware datetimes for Django
    cfg_start = result.config.start
    cfg_end = result.config.end
    if cfg_start.tzinfo is None:
        cfg_start = cfg_start.replace(tzinfo=UTC)
    if cfg_end.tzinfo is None:
        cfg_end = cfg_end.replace(tzinfo=UTC)

    run = BacktestRun.objects.create(
        strategy=result.config.strategy,
        instrument=result.config.instrument,
        start=cfg_start,
        end=cfg_end,
        bar_type=result.config.bar_type,
        extra_bar_types=result.config.extra_bar_types,
        trade_size=float(result.config.trade_size),
        balance=result.config.balance,
        leverage=result.config.leverage,
        strategy_params=result.config.strategy_params,
        total_pnl=result.total_pnl,
        total_return_pct=result.total_return_pct,
        num_trades=result.num_trades,
        win_rate=result.win_rate,
        max_drawdown_pct=result.max_drawdown_pct,
        avg_trade_pnl=result.avg_trade_pnl,
        avg_win=result.avg_win,
        avg_loss=result.avg_loss,
        profit_factor=result.profit_factor,
        duration_seconds=result.duration_seconds,
    )

    Trade.objects.bulk_create([
        Trade(
            run=run,
            instrument=t.instrument,
            side=t.side,
            quantity=t.quantity,
            open_price=t.open_price,
            close_price=t.close_price,
            realized_pnl=t.realized_pnl,
            realized_return_pct=t.realized_return_pct,
            opened_at=t.opened_at,
            closed_at=t.closed_at,
            duration_seconds=t.duration_seconds,
        )
        for t in result.trades
    ])

    EquitySnapshot.objects.bulk_create([
        EquitySnapshot(run=run, timestamp=ep.timestamp, balance=ep.balance)
        for ep in result.equity_curve
    ])


if __name__ == "__main__":
    main()
