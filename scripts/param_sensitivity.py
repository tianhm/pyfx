"""Parameter sensitivity sweep for CobanReborn strategy.

Perturbs key parameters by +/- 20-40% around defaults to check if the
edge is robust or sitting on a fragile peak. If P&L falls off a cliff
with small parameter changes, the strategy is likely curve-fit.

Usage:
    uv run python scripts/param_sensitivity.py [--data-file PATH] [--instrument PAIR]
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
START = datetime(2025, 1, 1)
END = datetime(2026, 3, 31)


# Each entry: (param_name, [values_to_test], label_format)
# The "default" value is always included and marked with (*)
PARAM_SWEEPS: list[tuple[str, list[object], str]] = [
    # SMA periods
    ("sma_fast_period", [2, 3, 4, 5, 6], "SMA fast={}"),
    ("sma_slow_period", [6, 7, 9, 11, 13], "SMA slow={}"),
    # ATR multipliers
    ("atr_tp_multiplier", [1.0, 1.5, 2.0, 2.5, 3.0], "ATR TP={}"),
    ("atr_sl_multiplier", [0.75, 1.0, 1.5, 2.0, 2.5], "ATR SL={}"),
    # RSI threshold
    ("rsi_level_threshold", [0.40, 0.45, 0.50, 0.55, 0.60], "RSI thresh={}"),
    # Signal window
    ("max_signal_window_seconds", [1800, 3600, 5400, 7200], "Sig window={}s"),
    # Filter staleness
    ("filter_staleness_seconds", [3600, 5400, 7200, 10800, 14400], "Stale window={}s"),
]

# Defaults for reference
DEFAULTS: dict[str, object] = {
    "sma_fast_period": 4,
    "sma_slow_period": 9,
    "atr_tp_multiplier": 2.0,
    "atr_sl_multiplier": 1.5,
    "rsi_level_threshold": 0.50,
    "max_signal_window_seconds": 3600,
    "filter_staleness_seconds": 7200,
}


def _run_one(
    bars_df: pd.DataFrame,
    instrument: str,
    extra_params: dict,
    trade_size: Decimal,
) -> tuple[int, float, float | None, float]:
    """Run a single backtest and return (trades, pnl, pf, win_rate)."""
    config = BacktestConfig(
        strategy="coban_reborn",
        instrument=instrument,
        start=START,
        end=END,
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
            **extra_params,
        },
    )
    result = run_backtest(config, bars_df)
    return result.num_trades, result.total_pnl, result.profit_factor, result.win_rate


def main() -> None:
    parser = argparse.ArgumentParser(description="Parameter sensitivity sweep")
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
    args = parser.parse_args()

    if not args.data_file.exists():
        print(f"Data file not found: {args.data_file}")
        sys.exit(1)

    print(f"Loading {args.data_file} ...")
    bars_df = pd.read_parquet(args.data_file)
    if bars_df.index.tzinfo is None:
        bars_df.index = bars_df.index.tz_localize("UTC")
    bars_df = bars_df.loc[str(START.date()):str(END.date())]
    print(f"  {len(bars_df):,} bars loaded ({bars_df.index[0]} to {bars_df.index[-1]})")

    # Run baseline first
    print("\n--- Baseline (all defaults) ---")
    t0 = time.monotonic()
    n_trades, pnl, pf, wr = _run_one(bars_df, args.instrument, {}, args.trade_size)
    elapsed = time.monotonic() - t0
    print(f"  Trades: {n_trades:,}  P&L: ${pnl:,.0f}  "
          f"PF: {pf or 0:.2f}  WR: {wr:.1%}  ({elapsed:.1f}s)")
    baseline_pnl = pnl

    # Sweep each parameter
    for param_name, values, label_fmt in PARAM_SWEEPS:
        print(f"\n{'='*60}")
        print(f"Sweeping: {param_name}")
        print(f"{'='*60}")
        print(f"{'Value':<15} {'Trades':>7} {'P&L':>12} {'vs Base':>10} {'PF':>7} {'WR':>7}")
        print("-" * 60)

        for val in values:
            is_default = val == DEFAULTS.get(param_name)
            extra = {param_name: val}

            t0 = time.monotonic()
            n_trades, pnl, pf, wr = _run_one(
                bars_df, args.instrument, extra, args.trade_size,
            )
            elapsed = time.monotonic() - t0

            delta = pnl - baseline_pnl
            delta_str = f"{'+'if delta>=0 else ''}{delta:,.0f}"
            marker = " (*)" if is_default else ""
            print(
                f"{str(val):<15} {n_trades:>7,} {pnl:>11,.0f} {delta_str:>10} "
                f"{(pf or 0):>6.2f} {wr:>6.1%}{marker}"
            )

    print(f"\n(*) = default value  |  Baseline P&L: ${baseline_pnl:,.0f}")


if __name__ == "__main__":
    main()
