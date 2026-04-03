"""Multi-pair backtest sweep for CobanExperimental top variations.

Tests the best entry/exit combinations across multiple instruments:
EUR/USD, USD/JPY, GBP/USD, XAU/USD, BCO/USD (Brent Oil).

Usage:
    uv run python scripts/coban_multi_pair.py
"""

from __future__ import annotations

import sys
import time
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pyfx.backtest.runner import run_backtest
from pyfx.core.types import BacktestConfig


DATA_DIR = Path.home() / ".pyfx" / "data"
START = datetime(2025, 1, 1)
END = datetime(2025, 3, 31)  # Shortest common range across all pairs


# Instrument configs: (instrument_str, data_file, pip_scale_factor)
# pip_scale_factor adjusts TP/SL/spread_pips for instruments where
# default_fx_ccy creates wrong pip sizes.
# For FX pairs: pip = price_increment * 10 = 0.0001 -> 1x scale
# For XAU/USD: pip should be ~0.10 (10 cents), but engine uses 0.0001
#   -> need to multiply TP/SL by ~1000 to get equivalent dollar moves
# For BCO/USD: pip should be ~0.01, but engine uses 0.0001
#   -> need to multiply TP/SL by ~100
#
# However, the real approach is to think in terms of dollar moves:
# EUR/USD 10 pips = $0.0010 move on a ~1.08 price = 0.09% move
# XAU/USD equivalent = 3000 * 0.0009 = $2.70 move = 270 "pips" in engine terms
# Oil equivalent = 75 * 0.0009 = $0.07 move = 70 "pips" in engine terms
# USD/JPY: pip = 0.01 (3 dec), engine pip = 0.01, but default_fx_ccy uses 3 dec
#   -> 10 pips = 0.10 JPY move on ~155 = 0.065% = close enough

INSTRUMENTS: list[dict] = [
    {
        "name": "EUR/USD",
        "instrument": "EUR/USD",
        "data_file": DATA_DIR / "EURUSD_2025-2026_M1.parquet",
        "spread_pips": 1.5,
        "tp_pips": 10,
        "sl_pips": 30,
        "tp_pips_rr": 30,
        "sl_pips_rr": 15,
        "trailing_pips": 15,
    },
    {
        "name": "USD/JPY",
        "instrument": "USD/JPY",
        "data_file": DATA_DIR / "USDJPY_2025-2026_M1.parquet",
        # JPY pairs: price_increment=0.001, pip=0.01
        # 10 pips on JPY pair = 0.10 yen = same relative scale
        "spread_pips": 1.5,
        "tp_pips": 10,
        "sl_pips": 30,
        "tp_pips_rr": 30,
        "sl_pips_rr": 15,
        "trailing_pips": 15,
    },
    {
        "name": "GBP/USD",
        "instrument": "GBP/USD",
        "data_file": DATA_DIR / "GBPUSD_2025-2026_M1.parquet",
        "spread_pips": 2.0,  # GBP spreads slightly wider
        "tp_pips": 10,
        "sl_pips": 30,
        "tp_pips_rr": 30,
        "sl_pips_rr": 15,
        "trailing_pips": 15,
    },
    {
        "name": "XAU/USD",
        "instrument": "XAU/USD",
        "data_file": DATA_DIR / "XAUUSD_2025-2026_M1.parquet",
        # Gold: price ~3000, engine pip = 0.0001
        # To get equivalent % moves as EUR/USD 10-pip (0.09%):
        # 3000 * 0.0009 / 0.0001 = 27000 pips... too much
        # Better: use ATR-based exits for gold (adapts automatically)
        # For fixed: gold moves ~$30/day, 10-pip EUR = ~$1 move
        # Equivalent gold move: ~$3 = 30000 engine pips
        # Let's use more reasonable: 300 pips = $0.03 move (small scalp)
        "spread_pips": 30,  # ~$0.003 spread (realistic for gold)
        "tp_pips": 300,
        "sl_pips": 900,
        "tp_pips_rr": 900,
        "sl_pips_rr": 450,
        "trailing_pips": 450,
    },
    {
        "name": "OIL/USD",
        "instrument": "OIL/USD",
        "data_file": DATA_DIR / "OILUSD_2025-2026_M1.parquet",
        # Oil: price ~75, engine pip = 0.0001
        # Equivalent: 75 * 0.0009 / 0.0001 = 675 pips
        # Oil moves ~$2/day. Use ~$0.10 = 1000 pips as TP equivalent
        "spread_pips": 30,  # ~$0.003 spread
        "tp_pips": 100,
        "sl_pips": 300,
        "tp_pips_rr": 300,
        "sl_pips_rr": 150,
        "trailing_pips": 150,
    },
]

# Top variations to test per instrument
def get_variations(inst: dict) -> list[tuple[str, dict, list[str] | None]]:
    """Generate variation configs scaled to the instrument."""
    tp = inst["tp_pips"]
    sl = inst["sl_pips"]
    tp_rr = inst["tp_pips_rr"]
    sl_rr = inst["sl_pips_rr"]
    trail = inst["trailing_pips"]
    spread = inst["spread_pips"]

    return [
        # 1h/2h timeframes
        ("TF fixed (1h/2h)", {
            "entry_mode": "trend_follow",
            "take_profit_pips": tp, "stop_loss_pips": sl,
            "spread_pips": spread,
        }, None),
        ("TF+ATR (1h/2h)", {
            "entry_mode": "trend_follow", "exit_mode": "atr",
            "spread_pips": spread,
        }, None),
        ("TF+R:R (1h/2h)", {
            "entry_mode": "trend_follow",
            "take_profit_pips": tp_rr, "stop_loss_pips": sl_rr,
            "spread_pips": spread,
        }, None),
        ("TF+Trail (1h/2h)", {
            "entry_mode": "trend_follow", "exit_mode": "trailing",
            "trailing_stop_pips": trail, "stop_loss_pips": sl,
            "spread_pips": spread,
        }, None),
        # 5m/15m timeframes
        ("TF fixed (5m/15m)", {
            "entry_mode": "trend_follow",
            "take_profit_pips": tp, "stop_loss_pips": sl,
            "spread_pips": spread,
        }, ["5-MINUTE-LAST-EXTERNAL", "15-MINUTE-LAST-EXTERNAL"]),
        ("TF+ATR (5m/15m)", {
            "entry_mode": "trend_follow", "exit_mode": "atr",
            "spread_pips": spread,
        }, ["5-MINUTE-LAST-EXTERNAL", "15-MINUTE-LAST-EXTERNAL"]),
        ("TF+R:R (5m/15m)", {
            "entry_mode": "trend_follow",
            "take_profit_pips": tp_rr, "stop_loss_pips": sl_rr,
            "spread_pips": spread,
        }, ["5-MINUTE-LAST-EXTERNAL", "15-MINUTE-LAST-EXTERNAL"]),
        ("TF+Trail (5m/15m)", {
            "entry_mode": "trend_follow", "exit_mode": "trailing",
            "trailing_stop_pips": trail, "stop_loss_pips": sl,
            "spread_pips": spread,
        }, ["5-MINUTE-LAST-EXTERNAL", "15-MINUTE-LAST-EXTERNAL"]),
    ]


def load_data(data_file: Path) -> pd.DataFrame:
    """Load instrument M1 data."""
    df = pd.read_parquet(data_file)
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
    instrument: str,
    extra_bar_types: list[str] | None = None,
) -> dict:
    """Run a single backtest variation."""
    if extra_bar_types is None:
        extra_bar_types = ["60-MINUTE-LAST-EXTERNAL", "120-MINUTE-LAST-EXTERNAL"]

    config = BacktestConfig(
        strategy="coban_experimental",
        instrument=instrument,
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


def print_results(results: list[dict], label: str) -> None:
    """Print a formatted comparison table."""
    print("\n" + "=" * 120)
    print(f"{label} — Jan-Jun 2025")
    print("=" * 120)

    header = (
        f"{'Variation':<24} {'Trades':>6} {'WinR%':>6} {'P&L':>11} "
        f"{'Ret%':>7} {'AvgTrd':>9} {'AvgWin':>9} {'AvgLoss':>9} "
        f"{'MaxDD%':>7} {'PF':>6} {'Time':>5}"
    )
    print(header)
    print("-" * 120)

    for r in results:
        pf = f"{r['profit_factor']:.2f}" if r["profit_factor"] is not None else "N/A"
        print(
            f"{r['name']:<24} {r['trades']:>6} {r['win_rate']:>5.1f}% "
            f"{r['pnl']:>+11.2f} {r['return_pct']:>+6.2f}% "
            f"{r['avg_trade']:>+9.2f} {r['avg_win']:>+9.2f} {r['avg_loss']:>+9.2f} "
            f"{r['max_dd']:>6.2f}% {pf:>6} {r['time']:>4.1f}s"
        )

    print("=" * 120)

    traded = [r for r in results if r["trades"] > 0]
    if traded:
        best = max(traded, key=lambda r: r["pnl"])
        print(f"  Best by P&L: {best['name']} ({best['pnl']:+.2f})")


def main() -> None:
    all_results: dict[str, list[dict]] = {}

    for inst in INSTRUMENTS:
        name = inst["name"]
        data_file = inst["data_file"]

        if not data_file.exists():
            print(f"\n*** SKIPPING {name} — data file not found: {data_file}")
            continue

        print(f"\n{'#' * 60}")
        print(f"# {name}")
        print(f"{'#' * 60}")

        bars_df = load_data(data_file)
        print(f"Loaded {len(bars_df)} bars ({bars_df.index[0]} to {bars_df.index[-1]})")

        variations = get_variations(inst)
        results: list[dict] = []

        for i, (var_name, params, extra) in enumerate(variations, 1):
            print(f"  [{i}/{len(variations)}] {var_name}...", end=" ", flush=True)
            try:
                r = run_variation(var_name, params, bars_df, inst["instrument"], extra)
                results.append(r)
                print(f"{r['trades']} trades, P&L: {r['pnl']:+.2f}")
            except Exception as e:
                print(f"ERROR: {e}")
                results.append({
                    "name": var_name, "trades": 0, "win_rate": 0, "pnl": 0,
                    "return_pct": 0, "avg_trade": 0, "avg_win": 0, "avg_loss": 0,
                    "max_dd": 0, "profit_factor": None, "time": 0,
                })

        print_results(results, name)
        all_results[name] = results

    # Cross-instrument summary
    if all_results:
        print("\n\n" + "=" * 80)
        print("CROSS-INSTRUMENT SUMMARY — Best variation per instrument")
        print("=" * 80)
        print(f"{'Instrument':<12} {'Best Variation':<24} {'Trades':>6} {'P&L':>11} {'WR%':>6} {'MaxDD':>7} {'PF':>6}")
        print("-" * 80)
        for inst_name, results in all_results.items():
            traded = [r for r in results if r["trades"] > 0]
            if traded:
                best = max(traded, key=lambda r: r["pnl"])
                pf = f"{best['profit_factor']:.2f}" if best["profit_factor"] is not None else "N/A"
                print(
                    f"{inst_name:<12} {best['name']:<24} {best['trades']:>6} "
                    f"{best['pnl']:>+11.2f} {best['win_rate']:>5.1f}% "
                    f"{best['max_dd']:>6.2f}% {pf:>6}"
                )
            else:
                print(f"{inst_name:<12} {'(no trades)':<24}")
        print("=" * 80)


if __name__ == "__main__":
    main()
