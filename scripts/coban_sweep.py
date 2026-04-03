"""Backtest sweep for CobanReborn entry/exit variations.

Runs 10+ configurations of the CobanExperimental strategy and prints
a comparison table to identify promising approaches.

Usage:
    uv run python scripts/coban_sweep.py
"""

from __future__ import annotations

import sys
import time
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pandas as pd

# Ensure project is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pyfx.backtest.runner import run_backtest
from pyfx.core.types import BacktestConfig


DATA_FILE = Path.home() / ".pyfx" / "data" / "EURUSD_2025-2026_M1.parquet"
START = datetime(2025, 1, 1)
END = datetime(2025, 6, 30)


# Each variation is (name, strategy_params dict)
VARIATIONS: list[tuple[str, dict]] = [
    # --- Entry variations ---
    (
        "1. Relaxed (no M1/DC)",
        {"entry_mode": "relaxed"},
    ),
    (
        "2. 2-of-3 H1 signals",
        {"entry_mode": "2of3"},
    ),
    (
        "3. No H2 confirm",
        {"entry_mode": "no_h2"},
    ),
    (
        "4. Wide window (4h)",
        {"entry_mode": "relaxed", "max_signal_window_seconds": 14400},
    ),
    (
        "5. SMA + MACD only",
        {"entry_mode": "sma_macd"},
    ),
    (
        "6. RSI level filter",
        {"entry_mode": "rsi_level"},
    ),
    (
        "7. Trend follow",
        {"entry_mode": "trend_follow"},
    ),
    # --- Exit variations (using sma_macd entry since it should produce trades) ---
    (
        "8. Trailing stop",
        {"entry_mode": "sma_macd", "exit_mode": "trailing", "trailing_stop_pips": 15},
    ),
    (
        "9. Better R:R (30:15)",
        {"entry_mode": "sma_macd", "take_profit_pips": 30, "stop_loss_pips": 15},
    ),
    (
        "10. ATR exits",
        {"entry_mode": "sma_macd", "exit_mode": "atr", "atr_tp_multiplier": 2.0, "atr_sl_multiplier": 1.5},
    ),
]


def load_data() -> pd.DataFrame:
    """Load EURUSD M1 data."""
    df = pd.read_parquet(DATA_FILE)
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    mask = (df.index >= pd.Timestamp(START, tz="UTC")) & (
        df.index < pd.Timestamp(END, tz="UTC")
    )
    return df.loc[mask]


def run_variation(
    name: str,
    params: dict,
    bars_df: pd.DataFrame,
    extra_bar_types: list[str] | None = None,
) -> dict:
    """Run a single backtest variation and return results."""
    if extra_bar_types is None:
        extra_bar_types = ["60-MINUTE-LAST-EXTERNAL", "120-MINUTE-LAST-EXTERNAL"]

    config = BacktestConfig(
        strategy="coban_experimental",
        instrument="EUR/USD",
        start=START,
        end=END,
        bar_type="1-MINUTE-LAST-EXTERNAL",
        extra_bar_types=extra_bar_types,
        trade_size=Decimal("100000"),
        strategy_params=params,
    )

    t0 = time.monotonic()
    result = run_backtest(config, bars_df)
    elapsed = time.monotonic() - t0

    return {
        "name": name,
        "trades": result.num_trades,
        "win_rate": result.win_rate,
        "pnl": result.total_pnl,
        "return_pct": result.total_return_pct,
        "avg_trade": result.avg_trade_pnl,
        "avg_win": result.avg_win,
        "avg_loss": result.avg_loss,
        "max_dd": result.max_drawdown_pct,
        "profit_factor": result.profit_factor,
        "time": elapsed,
    }


TF_5_15 = ["5-MINUTE-LAST-EXTERNAL", "15-MINUTE-LAST-EXTERNAL"]

# 5min/15min variations — same entry modes
VARIATIONS_5M15M: list[tuple[str, dict]] = [
    ("1. Relaxed 5m/15m", {"entry_mode": "relaxed"}),
    ("2. 2-of-3 5m/15m", {"entry_mode": "2of3"}),
    ("3. No H2 5m/15m", {"entry_mode": "no_h2"}),
    ("4. Wide win 5m/15m", {"entry_mode": "relaxed", "max_signal_window_seconds": 14400}),
    ("5. SMA+MACD 5m/15m", {"entry_mode": "sma_macd"}),
    ("6. RSI lvl 5m/15m", {"entry_mode": "rsi_level"}),
    ("7. Trend fol 5m/15m", {"entry_mode": "trend_follow"}),
    ("8. Trail 5m/15m", {"entry_mode": "sma_macd", "exit_mode": "trailing", "trailing_stop_pips": 15}),
    ("9. R:R 30:15 5m/15m", {"entry_mode": "sma_macd", "take_profit_pips": 30, "stop_loss_pips": 15}),
    ("10. ATR 5m/15m", {"entry_mode": "sma_macd", "exit_mode": "atr", "atr_tp_multiplier": 2.0, "atr_sl_multiplier": 1.5}),
]

# Combined best entry + best exit variations
COMBINED: list[tuple[str, dict, list[str] | None]] = [
    # Trend follow + ATR exits on 1h/2h
    ("TF + ATR (1h/2h)", {"entry_mode": "trend_follow", "exit_mode": "atr"}, None),
    # Trend follow + trailing on 1h/2h
    ("TF + Trail (1h/2h)", {"entry_mode": "trend_follow", "exit_mode": "trailing", "trailing_stop_pips": 15}, None),
    # Trend follow + better R:R on 1h/2h
    ("TF + R:R 30:15 (1h/2h)", {"entry_mode": "trend_follow", "take_profit_pips": 30, "stop_loss_pips": 15}, None),
    # Trend follow + ATR on 5m/15m
    ("TF + ATR (5m/15m)", {"entry_mode": "trend_follow", "exit_mode": "atr"}, TF_5_15),
    # Trend follow + trailing on 5m/15m
    ("TF + Trail (5m/15m)", {"entry_mode": "trend_follow", "exit_mode": "trailing", "trailing_stop_pips": 15}, TF_5_15),
    # Trend follow + better R:R on 5m/15m
    ("TF + R:R 30:15 (5m/15m)", {"entry_mode": "trend_follow", "take_profit_pips": 30, "stop_loss_pips": 15}, TF_5_15),
    # Wide window + ATR on 1h/2h
    ("Wide + ATR (1h/2h)", {"entry_mode": "relaxed", "max_signal_window_seconds": 14400, "exit_mode": "atr"}, None),
    # 2-of-3 + ATR on 1h/2h
    ("2of3 + ATR (1h/2h)", {"entry_mode": "2of3", "exit_mode": "atr"}, None),
]


def _run_batch(
    label: str,
    variations: list[tuple],
    bars_df: pd.DataFrame,
    default_extra: list[str] | None = None,
) -> list[dict]:
    """Run a batch of variations and print results."""
    results: list[dict] = []
    total = len(variations)

    for i, item in enumerate(variations, 1):
        if len(item) == 2:
            name, params = item
            extra = default_extra
        else:
            name, params, extra = item

        print(f"\n[{i}/{total}] Running: {name}...")
        try:
            r = run_variation(name, params, bars_df, extra_bar_types=extra)
            results.append(r)
            print(f"  -> {r['trades']} trades, P&L: {r['pnl']:+.2f}, WR: {r['win_rate']:.1f}%")
        except Exception as e:
            print(f"  -> ERROR: {e}")
            results.append({
                "name": name, "trades": 0, "win_rate": 0, "pnl": 0,
                "return_pct": 0, "avg_trade": 0, "avg_win": 0, "avg_loss": 0,
                "max_dd": 0, "profit_factor": None, "time": 0,
            })

    print_results(results, label)
    return results


def print_results(results: list[dict], label: str = "") -> None:
    """Print a formatted comparison table."""
    title = f"COBAN SWEEP — {label}" if label else "COBAN STRATEGY VARIATION SWEEP"
    print("\n" + "=" * 120)
    print(f"{title} — EURUSD Jan-Jun 2025")
    print("=" * 120)

    header = (
        f"{'Variation':<28} {'Trades':>6} {'WinR%':>6} {'P&L':>10} "
        f"{'Ret%':>7} {'AvgTrd':>9} {'AvgWin':>9} {'AvgLoss':>9} "
        f"{'MaxDD%':>7} {'PF':>6} {'Time':>5}"
    )
    print(header)
    print("-" * 120)

    for r in results:
        pf = f"{r['profit_factor']:.2f}" if r["profit_factor"] is not None else "N/A"
        print(
            f"{r['name']:<28} {r['trades']:>6} {r['win_rate']:>5.1f}% "
            f"{r['pnl']:>+10.2f} {r['return_pct']:>+6.2f}% "
            f"{r['avg_trade']:>+9.2f} {r['avg_win']:>+9.2f} {r['avg_loss']:>+9.2f} "
            f"{r['max_dd']:>6.2f}% {pf:>6} {r['time']:>4.1f}s"
        )

    print("=" * 120)

    traded = [r for r in results if r["trades"] > 0]
    if traded:
        best = max(traded, key=lambda r: r["pnl"])
        print(f"\nBest by P&L: {best['name']} ({best['pnl']:+.2f})")
        best_wr = max(traded, key=lambda r: r["win_rate"])
        print(f"Best by Win Rate: {best_wr['name']} ({best_wr['win_rate']:.1f}%)")
    else:
        print("\nNo variations produced trades!")


def main() -> None:
    print(f"Loading data from {DATA_FILE}...")
    bars_df = load_data()
    print(f"Loaded {len(bars_df)} bars ({bars_df.index[0]} to {bars_df.index[-1]})")

    # Group 1: Original 1h/2h
    print("\n\n" + "#" * 60)
    print("# GROUP 1: 1h/2h timeframes (original)")
    print("#" * 60)
    _run_batch("1h/2h", VARIATIONS, bars_df)

    # Group 2: 5min/15min timeframes
    print("\n\n" + "#" * 60)
    print("# GROUP 2: 5min/15min timeframes")
    print("#" * 60)
    _run_batch("5m/15m", VARIATIONS_5M15M, bars_df, default_extra=TF_5_15)

    # Group 3: Combined best entry + best exit
    print("\n\n" + "#" * 60)
    print("# GROUP 3: Combined best entry + best exit")
    print("#" * 60)
    _run_batch("COMBINED", COMBINED, bars_df)


if __name__ == "__main__":
    main()
