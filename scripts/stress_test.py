"""Multi-seed, multi-instrument stress test for the Money Maker config.

Tests the winning strategy across:
- 5 random seeds (to measure slippage sensitivity)
- All USD-quoted instruments with available data
- Maximum available date range per instrument
- Realistic spreads per instrument

Usage:
    uv run python scripts/stress_test.py
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
BALANCE = 5_000.0
LEVERAGE = 200.0

# Money Maker config (optimized params from sweep)
STRATEGY_PARAMS = {
    "entry_mode": "trend_follow",
    "exit_mode": "atr",
    "atr_tp_multiplier": 3.0,
    "atr_sl_multiplier": 2.0,
    "sma_fast_period": 3,
    "sma_slow_period": 7,
    "macd_fast_period": 8,
    "macd_slow_period": 21,
    "macd_signal_period": 5,
    "rsi_level_threshold": 0.50,
    "session_start_hour": 0,
    "session_end_hour": 24,
}

# Seeds to test: 42 (original), plus 4 others
SEEDS = [42, 1, 100, 777, 31337]

# Instruments with realistic spreads and position sizes
INSTRUMENTS = {
    "EUR/USD": {
        "file": DATA_DIR / "EURUSD_2025-2026_M1.parquet",
        "spread_pips": 1.5,
        "trade_size": Decimal("100000"),
    },
    "GBP/USD": {
        "file": DATA_DIR / "GBPUSD_2025-2026_M1.parquet",
        "spread_pips": 2.0,
        "trade_size": Decimal("100000"),
    },
    "XAU/USD": {
        "file": DATA_DIR / "XAUUSD_2025-2026_M1.parquet",
        "spread_pips": 4000.0,  # $0.40 realistic gold spread
        "trade_size": Decimal("100"),  # 100 oz realistic
    },
    "AUD/USD": {
        "file": DATA_DIR / "AUDUSD_2025-2026_M1.parquet",
        "spread_pips": 1.5,
        "trade_size": Decimal("100000"),
    },
    "NZD/USD": {
        "file": DATA_DIR / "NZDUSD_2025-2026_M1.parquet",
        "spread_pips": 2.0,
        "trade_size": Decimal("100000"),
    },
}

TF_5_15 = ["5-MINUTE-LAST-EXTERNAL", "15-MINUTE-LAST-EXTERNAL"]


def load_data(data_file: Path) -> pd.DataFrame:
    """Load full date range from parquet file."""
    df = pd.read_parquet(data_file)
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    return df


def run_single(
    instrument: str,
    params: dict,
    bars_df: pd.DataFrame,
    trade_size: Decimal,
    seed: int | None,
    start: datetime,
    end: datetime,
) -> dict:
    """Run a single backtest."""
    config = BacktestConfig(
        strategy="coban_reborn",
        instrument=instrument,
        start=start,
        end=end,
        bar_type="1-MINUTE-LAST-EXTERNAL",
        extra_bar_types=TF_5_15,
        trade_size=trade_size,
        balance=BALANCE,
        leverage=LEVERAGE,
        strategy_params=params,
        random_seed=seed,
    )

    t0 = time.monotonic()
    result = run_backtest(config, bars_df)
    elapsed = time.monotonic() - t0

    return {
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


def main() -> None:
    t_start = time.monotonic()

    print("=" * 100)
    print("MULTI-SEED STRESS TEST — Money Maker Config")
    print(f"Balance: ${BALANCE:,.0f}  |  Leverage: {LEVERAGE}x  |  Seeds: {SEEDS}")
    print("=" * 100)

    all_results: dict[str, list[dict]] = {}

    for inst_name, inst_info in INSTRUMENTS.items():
        data_file = inst_info["file"]
        if not data_file.exists():
            print(f"\n*** SKIPPING {inst_name} — no data")
            continue

        bars_df = load_data(data_file)
        start = bars_df.index[0].to_pydatetime()
        end = bars_df.index[-1].to_pydatetime()

        print(f"\n{'#' * 80}")
        print(f"# {inst_name}  |  {len(bars_df)} bars  |  {start.date()} to {end.date()}")
        print(f"# spread={inst_info['spread_pips']} pips  |  trade_size={inst_info['trade_size']}")
        print(f"{'#' * 80}")

        params = {
            **STRATEGY_PARAMS,
            "spread_pips": inst_info["spread_pips"],
        }

        results = []
        for seed in SEEDS:
            label = f"seed={seed}"
            print(f"  [{label}]...", end=" ", flush=True)
            try:
                r = run_single(
                    instrument=inst_name,
                    params=params,
                    bars_df=bars_df,
                    trade_size=inst_info["trade_size"],
                    seed=seed,
                    start=start,
                    end=end,
                )
                r["seed"] = seed
                results.append(r)
                pf = f"{r['profit_factor']:.2f}" if r['profit_factor'] is not None else "N/A"
                print(
                    f"{r['trades']} trades, P&L: ${r['pnl']:+,.2f}, "
                    f"WR: {r['win_rate']:.1%}, PF: {pf}, DD: {r['max_dd']:.2f}%"
                )
            except Exception as e:
                print(f"ERROR: {e}")

        # Also run with random seed
        print(f"  [random]...", end=" ", flush=True)
        try:
            r = run_single(
                instrument=inst_name,
                params=params,
                bars_df=bars_df,
                trade_size=inst_info["trade_size"],
                seed=None,
                start=start,
                end=end,
            )
            r["seed"] = "random"
            results.append(r)
            pf = f"{r['profit_factor']:.2f}" if r['profit_factor'] is not None else "N/A"
            print(
                f"{r['trades']} trades, P&L: ${r['pnl']:+,.2f}, "
                f"WR: {r['win_rate']:.1%}, PF: {pf}, DD: {r['max_dd']:.2f}%"
            )
        except Exception as e:
            print(f"ERROR: {e}")

        all_results[inst_name] = results

        # Print summary for this instrument
        if results:
            pnls = [r["pnl"] for r in results]
            pfs = [r["profit_factor"] for r in results if r["profit_factor"] is not None]
            dds = [r["max_dd"] for r in results]
            wrs = [r["win_rate"] for r in results]

            print(f"\n  --- {inst_name} Summary ({len(results)} runs) ---")
            print(f"  P&L:   min=${min(pnls):+,.2f}  max=${max(pnls):+,.2f}  "
                  f"avg=${sum(pnls)/len(pnls):+,.2f}  spread=${max(pnls)-min(pnls):,.2f}")
            if pfs:
                print(f"  PF:    min={min(pfs):.2f}  max={max(pfs):.2f}  avg={sum(pfs)/len(pfs):.2f}")
            print(f"  MaxDD: min={min(dds):.2f}%  max={max(dds):.2f}%")
            print(f"  WR:    min={min(wrs):.1%}  max={max(wrs):.1%}")

            # Variance check
            avg_pnl = sum(pnls) / len(pnls)
            if avg_pnl != 0:
                variance_pct = (max(pnls) - min(pnls)) / abs(avg_pnl) * 100
                print(f"  P&L variance: {variance_pct:.1f}% of avg (>10% = slippage-sensitive)")

    # Cross-instrument summary
    print("\n\n" + "=" * 100)
    print("CROSS-INSTRUMENT SUMMARY")
    print("=" * 100)
    print(f"{'Instrument':<10} {'Trades':>6} {'Avg P&L':>12} {'Avg Ret%':>9} "
          f"{'Avg PF':>7} {'Avg WR':>7} {'Worst DD':>9} {'P&L Var%':>9}")
    print("-" * 80)

    for inst_name, results in all_results.items():
        if not results:
            continue
        pnls = [r["pnl"] for r in results]
        rets = [r["return_pct"] for r in results]
        pfs = [r["profit_factor"] for r in results if r["profit_factor"] is not None]
        wrs = [r["win_rate"] for r in results]
        dds = [r["max_dd"] for r in results]
        trades = results[0]["trades"]  # same across seeds
        avg_pnl = sum(pnls) / len(pnls)
        avg_ret = sum(rets) / len(rets)
        avg_pf = sum(pfs) / len(pfs) if pfs else 0
        avg_wr = sum(wrs) / len(wrs)
        worst_dd = min(dds)
        var_pct = (max(pnls) - min(pnls)) / abs(avg_pnl) * 100 if avg_pnl != 0 else 0

        print(
            f"{inst_name:<10} {trades:>6} ${avg_pnl:>+11,.2f} {avg_ret:>+8.2f}% "
            f"{avg_pf:>6.2f} {avg_wr:>6.1%} {worst_dd:>8.2f}% {var_pct:>8.1f}%"
        )

    print("=" * 100)
    elapsed = time.monotonic() - t_start
    print(f"\nTotal time: {elapsed:.1f}s ({elapsed/60:.1f} min)")


if __name__ == "__main__":
    main()
