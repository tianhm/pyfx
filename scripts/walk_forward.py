"""Walk-forward analysis for CobanReborn strategy.

Splits data into rolling train/test windows and runs backtests on each
test window to verify the strategy isn't curve-fit to a specific period.

Usage:
    uv run python scripts/walk_forward.py [--data-file PATH] [--instrument PAIR]
    uv run python scripts/walk_forward.py --window-months 3 --step-months 1
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pyfx.backtest.runner import run_backtest
from pyfx.core.types import BacktestConfig

DEFAULT_DATA_FILE = Path.home() / ".pyfx" / "data" / "EURUSD_2025-2026_M1.parquet"
DEFAULT_INSTRUMENT = "EUR/USD"


def _run_window(
    bars_df: pd.DataFrame,
    instrument: str,
    start: datetime,
    end: datetime,
    trade_size: Decimal,
    extra_params: dict | None = None,
) -> tuple[int, float, float | None, float, float]:
    """Run a single backtest window. Returns (trades, pnl, pf, wr, max_dd)."""
    window_df = bars_df.loc[str(start.date()):str(end.date())]
    if len(window_df) < 100:  # noqa: PLR2004
        return 0, 0.0, None, 0.0, 0.0

    config = BacktestConfig(
        strategy="coban_reborn",
        instrument=instrument,
        start=start,
        end=end,
        bar_type="1-MINUTE-LAST-EXTERNAL",
        extra_bar_types=[
            "5-MINUTE-LAST-EXTERNAL",
            "15-MINUTE-LAST-EXTERNAL",
        ],
        trade_size=trade_size,
        strategy_params={
            "entry_mode": "trend_follow",
            "exit_mode": "atr",
            "session_start_hour": 0,
            "session_end_hour": 24,
            "spread_pips": 1.5,
            **(extra_params or {}),
        },
    )
    result = run_backtest(config, window_df)
    return (
        result.num_trades,
        result.total_pnl,
        result.profit_factor,
        result.win_rate,
        result.max_drawdown_pct,
    )


def _add_months(dt: datetime, months: int) -> datetime:
    """Add months to a datetime, clamping day to valid range."""
    month = dt.month + months
    year = dt.year + (month - 1) // 12
    month = ((month - 1) % 12) + 1
    import calendar
    max_day = calendar.monthrange(year, month)[1]
    day = min(dt.day, max_day)
    return dt.replace(year=year, month=month, day=day)


def main() -> None:
    parser = argparse.ArgumentParser(description="Walk-forward analysis")
    parser.add_argument(
        "--data-file", type=Path, default=DEFAULT_DATA_FILE,
        help="Path to M1 parquet data",
    )
    parser.add_argument(
        "--instrument", default=DEFAULT_INSTRUMENT,
        help="Instrument (e.g. EUR/USD, XAU/USD)",
    )
    parser.add_argument(
        "--trade-size", type=Decimal, default=Decimal("100000"),
        help="Trade size",
    )
    parser.add_argument(
        "--window-months", type=int, default=3,
        help="Test window length in months",
    )
    parser.add_argument(
        "--step-months", type=int, default=1,
        help="Step size in months between windows",
    )
    args = parser.parse_args()

    if not args.data_file.exists():
        print(f"Data file not found: {args.data_file}")
        sys.exit(1)

    print(f"Loading {args.data_file} ...")
    bars_df = pd.read_parquet(args.data_file)
    if bars_df.index.tzinfo is None:
        bars_df.index = bars_df.index.tz_localize("UTC")

    first_ts = bars_df.index[0].to_pydatetime()
    data_start = first_ts.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    data_end = bars_df.index[-1].to_pydatetime()
    print(f"  {len(bars_df):,} bars ({data_start.date()} to {data_end.date()})")

    # Generate windows
    windows: list[tuple[datetime, datetime]] = []
    window_start = data_start
    while True:
        window_end = _add_months(window_start, args.window_months)
        if window_end > data_end:
            break
        windows.append((window_start, window_end))
        window_start = _add_months(window_start, args.step_months)

    print(f"\n{len(windows)} windows of {args.window_months} months, "
          f"stepping {args.step_months} month(s)\n")

    # Header
    print(f"{'Window':<25} {'Trades':>7} {'P&L':>12} {'PF':>7} {'WR':>7} {'MaxDD':>8} {'Time':>6}")
    print("-" * 75)

    total_pnl = 0.0
    total_trades = 0
    profitable_windows = 0
    all_pfs: list[float] = []

    for w_start, w_end in windows:
        label = f"{w_start.strftime('%Y-%m')} → {w_end.strftime('%Y-%m')}"
        t0 = time.monotonic()
        n_trades, pnl, pf, wr, max_dd = _run_window(
            bars_df, args.instrument, w_start, w_end, args.trade_size,
        )
        elapsed = time.monotonic() - t0

        total_pnl += pnl
        total_trades += n_trades
        if pnl > 0:
            profitable_windows += 1
        if pf is not None:
            all_pfs.append(pf)

        pf_str = f"{pf:.2f}" if pf is not None else "N/A"
        print(
            f"{label:<25} {n_trades:>7,} {pnl:>11,.0f} {pf_str:>7} "
            f"{wr:>6.1%} {max_dd:>7.2f}% {elapsed:>5.1f}s"
        )

    # Summary
    print("-" * 75)
    avg_pf = sum(all_pfs) / len(all_pfs) if all_pfs else 0.0
    print("\nSummary:")
    print(f"  Windows: {len(windows)} total, {profitable_windows} profitable "
          f"({profitable_windows/len(windows)*100:.0f}%)")
    print(f"  Total trades: {total_trades:,}")
    print(f"  Total P&L: ${total_pnl:,.0f}")
    print(f"  Avg profit factor: {avg_pf:.2f}")

    if profitable_windows < len(windows) * 0.6:
        print("\n  ⚠  Less than 60% of windows profitable — edge may be period-specific")
    elif avg_pf < 1.2:  # noqa: PLR2004
        print("\n  ⚠  Average PF below 1.2 — thin edge, likely eroded by live costs")
    else:
        print(f"\n  ✓  {profitable_windows}/{len(windows)} windows profitable, "
              f"avg PF {avg_pf:.2f} — edge appears robust")


if __name__ == "__main__":
    main()
