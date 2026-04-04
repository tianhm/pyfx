"""Money Maker Sweep: Systematic parameter optimization for max returns.

Multi-phase greedy search across ATR multipliers, SMA periods, timeframes,
RSI/MACD tuning, and cross-instrument validation. Targets $5k balance with
high leverage (200x) to find configurations yielding 100%+ returns.

Usage:
    uv run python scripts/money_maker_sweep.py
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


# ── Constants ──────────────────────────────────────────────────────────────

DATA_DIR = Path.home() / ".pyfx" / "data"
START = datetime(2025, 1, 1)
END = datetime(2026, 3, 31)

BALANCE = 5_000.0
LEVERAGE = 200.0

# Instrument definitions (USD-quoted only for reliable P&L)
INSTRUMENTS = {
    "XAU/USD": {
        "file": DATA_DIR / "XAUUSD_2025-2026_M1.parquet",
        "spread_pips": 3.0,
        "trade_size": Decimal("100000"),  # 1 standard lot
    },
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

# Default 5m/15m timeframes (the known best)
TF_5_15 = ["5-MINUTE-LAST-EXTERNAL", "15-MINUTE-LAST-EXTERNAL"]

# Base strategy params (known best from research)
BASE_PARAMS = {
    "entry_mode": "trend_follow",
    "exit_mode": "atr",
    "atr_tp_multiplier": 2.0,
    "atr_sl_multiplier": 1.5,
    "sma_fast_period": 4,
    "sma_slow_period": 9,
    "rsi_level_threshold": 0.50,
    "session_start_hour": 0,
    "session_end_hour": 24,
}

# Hall of fame: accumulates best results across all phases
HALL_OF_FAME: list[dict] = []


# ── Helpers ────────────────────────────────────────────────────────────────

def load_data(data_file: Path) -> pd.DataFrame:
    """Load instrument M1 data for the full date range."""
    df = pd.read_parquet(data_file)
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    mask = (df.index >= pd.Timestamp(START, tz="UTC")) & (
        df.index < pd.Timestamp(END, tz="UTC")
    )
    return df.loc[mask]


def run_single(
    name: str,
    instrument: str,
    params: dict,
    bars_df: pd.DataFrame,
    extra_bar_types: list[str] | None = None,
    trade_size: Decimal = Decimal("100000"),
    balance: float = BALANCE,
    leverage: float = LEVERAGE,
) -> dict:
    """Run a single backtest and return results dict."""
    if extra_bar_types is None:
        extra_bar_types = TF_5_15

    config = BacktestConfig(
        strategy="coban_reborn",
        instrument=instrument,
        start=START,
        end=END,
        bar_type="1-MINUTE-LAST-EXTERNAL",
        extra_bar_types=extra_bar_types,
        trade_size=trade_size,
        balance=balance,
        leverage=leverage,
        strategy_params=params,
    )

    t0 = time.monotonic()
    result = run_backtest(config, bars_df)
    elapsed = time.monotonic() - t0

    return {
        "name": name,
        "instrument": instrument,
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
        "params": dict(params),
        "extra_bar_types": extra_bar_types,
        "trade_size": trade_size,
        "balance": balance,
        "leverage": leverage,
    }


def print_results(results: list[dict], title: str) -> None:
    """Print a formatted comparison table sorted by return %."""
    print("\n" + "=" * 130)
    print(f"{title}")
    print("=" * 130)

    header = (
        f"{'Variation':<30} {'Inst':<8} {'Trades':>6} {'WinR%':>6} {'P&L':>12} "
        f"{'Ret%':>9} {'AvgTrd':>9} {'AvgWin':>9} {'AvgLoss':>9} "
        f"{'MaxDD%':>7} {'PF':>6} {'Time':>5}"
    )
    print(header)
    print("-" * 130)

    # Sort by return_pct descending
    sorted_results = sorted(results, key=lambda r: r["return_pct"], reverse=True)

    for r in sorted_results:
        pf = f"{r['profit_factor']:.2f}" if r.get("profit_factor") is not None else "N/A"
        print(
            f"{r['name']:<30} {r.get('instrument', '?'):<8} {r['trades']:>6} {r['win_rate']:>5.1f}% "
            f"{r['pnl']:>+12.2f} {r['return_pct']:>+8.2f}% "
            f"{r['avg_trade']:>+9.2f} {r['avg_win']:>+9.2f} {r['avg_loss']:>+9.2f} "
            f"{r['max_dd']:>6.2f}% {pf:>6} {r['time']:>4.1f}s"
        )

    print("=" * 130)

    traded = [r for r in sorted_results if r["trades"] > 0]
    if traded:
        best = traded[0]  # Already sorted
        print(f"  BEST: {best['name']} on {best.get('instrument', '?')} -> {best['return_pct']:+.2f}% (P&L: {best['pnl']:+.2f}, PF: {best.get('profit_factor', 0):.2f})")


def run_phase(
    title: str,
    configs: list[tuple[str, str, dict, list[str] | None]],
    data_cache: dict[str, pd.DataFrame],
) -> list[dict]:
    """Run a phase of the sweep.

    configs: list of (name, instrument, params, extra_bar_types)
    """
    results: list[dict] = []
    total = len(configs)

    for i, (name, instrument, params, extra) in enumerate(configs, 1):
        inst_info = INSTRUMENTS[instrument]
        print(f"  [{i}/{total}] {name} on {instrument}...", end=" ", flush=True)

        if instrument not in data_cache:
            data_file = inst_info["file"]
            if not data_file.exists():
                print(f"SKIP (no data)")
                continue
            data_cache[instrument] = load_data(data_file)
            print(f"(loaded {len(data_cache[instrument])} bars) ", end="", flush=True)

        try:
            r = run_single(
                name=name,
                instrument=instrument,
                params={**params, "spread_pips": inst_info["spread_pips"]},
                bars_df=data_cache[instrument],
                extra_bar_types=extra,
                trade_size=inst_info["trade_size"],
            )
            results.append(r)
            HALL_OF_FAME.append(r)
            print(f"{r['trades']} trades, Ret: {r['return_pct']:+.2f}%, PF: {r.get('profit_factor', 0):.2f}")
        except Exception as e:
            print(f"ERROR: {e}")

    print_results(results, title)
    return results


def best_from(results: list[dict], min_trades: int = 20) -> dict | None:
    """Get the best result by return % with minimum trade count."""
    valid = [r for r in results if r["trades"] >= min_trades and r.get("profit_factor") is not None]
    if not valid:
        return None
    return max(valid, key=lambda r: r["return_pct"])


# ── Phase 1: Baseline at $5k ──────────────────────────────────────────────

def phase1_baseline(data_cache: dict[str, pd.DataFrame]) -> dict:
    """Confirm known-best config works at $5k/200x."""
    print("\n\n" + "#" * 70)
    print("# PHASE 1: Baseline — Known best at $5k / 200x leverage")
    print("#" * 70)

    configs = []
    for instrument in ["XAU/USD", "EUR/USD", "GBP/USD"]:
        for ts_label, ts_mult in [("conservative", 0.5), ("standard", 1.0), ("aggressive", 2.0)]:
            base_ts = INSTRUMENTS[instrument]["trade_size"]
            ts = Decimal(str(int(float(base_ts) * ts_mult)))
            name = f"Baseline {ts_label} ({ts})"
            configs.append((name, instrument, dict(BASE_PARAMS), TF_5_15))

    # We need to handle different trade_sizes, so run manually
    results: list[dict] = []
    total = len(configs)

    for i, (name, instrument, params, extra) in enumerate(configs, 1):
        inst_info = INSTRUMENTS[instrument]
        base_ts = inst_info["trade_size"]

        if "conservative" in name:
            ts = Decimal(str(int(float(base_ts) * 0.5)))
        elif "aggressive" in name:
            ts = Decimal(str(int(float(base_ts) * 2.0)))
        else:
            ts = base_ts

        print(f"  [{i}/{total}] {name} on {instrument} (size={ts})...", end=" ", flush=True)

        if instrument not in data_cache:
            data_file = inst_info["file"]
            if not data_file.exists():
                print(f"SKIP (no data)")
                continue
            data_cache[instrument] = load_data(data_file)
            print(f"(loaded {len(data_cache[instrument])} bars) ", end="", flush=True)

        try:
            r = run_single(
                name=name,
                instrument=instrument,
                params={**params, "spread_pips": inst_info["spread_pips"]},
                bars_df=data_cache[instrument],
                extra_bar_types=extra,
                trade_size=ts,
            )
            results.append(r)
            HALL_OF_FAME.append(r)
            print(f"{r['trades']} trades, Ret: {r['return_pct']:+.2f}%, PF: {r.get('profit_factor', 0):.2f}")
        except Exception as e:
            print(f"ERROR: {e}")

    print_results(results, "PHASE 1: Baseline at $5k / 200x leverage")

    best = best_from(results)
    if best:
        print(f"\n  >>> Phase 1 winner: {best['name']} on {best['instrument']} = {best['return_pct']:+.2f}%")
    return best or {}


# ── Phase 2: ATR Multiplier Grid ──────────────────────────────────────────

def phase2_atr_grid(data_cache: dict[str, pd.DataFrame], carry: dict) -> dict:
    """Sweep ATR TP/SL multipliers."""
    print("\n\n" + "#" * 70)
    print("# PHASE 2: ATR Multiplier Grid Search")
    print("#" * 70)

    atr_combos = [
        (1.5, 0.8), (1.5, 1.0), (1.5, 1.5),
        (2.0, 0.8), (2.0, 1.0), (2.0, 1.5), (2.0, 2.0),
        (2.5, 1.0), (2.5, 1.5), (2.5, 2.0),
        (3.0, 1.0), (3.0, 1.5), (3.0, 2.0),
    ]

    configs = []
    for instrument in ["XAU/USD", "EUR/USD"]:
        for tp, sl in atr_combos:
            params = {**BASE_PARAMS, "atr_tp_multiplier": tp, "atr_sl_multiplier": sl}
            name = f"ATR TP={tp} SL={sl}"
            configs.append((name, instrument, params, TF_5_15))

    results = run_phase("PHASE 2: ATR Multiplier Grid", configs, data_cache)

    best = best_from(results)
    if best:
        print(f"\n  >>> Phase 2 winner: {best['name']} on {best['instrument']} = {best['return_pct']:+.2f}%")
        # Extract best ATR params to carry forward
        carry["atr_tp_multiplier"] = best["params"]["atr_tp_multiplier"]
        carry["atr_sl_multiplier"] = best["params"]["atr_sl_multiplier"]
    return best or carry


# ── Phase 3: SMA Period Sweep ─────────────────────────────────────────────

def phase3_sma_sweep(data_cache: dict[str, pd.DataFrame], carry: dict) -> dict:
    """Sweep SMA fast/slow period combinations."""
    print("\n\n" + "#" * 70)
    print("# PHASE 3: SMA Period Sweep")
    print("#" * 70)

    sma_pairs = [
        (3, 7), (3, 9), (4, 9), (4, 12),
        (5, 10), (5, 13), (6, 12), (6, 15),
        (8, 13), (8, 21), (10, 20), (10, 30),
        (13, 34),
    ]

    configs = []
    for instrument in ["XAU/USD", "EUR/USD"]:
        for fast, slow in sma_pairs:
            params = {**BASE_PARAMS}
            # Apply carried-forward ATR params
            if "atr_tp_multiplier" in carry:
                params["atr_tp_multiplier"] = carry["atr_tp_multiplier"]
            if "atr_sl_multiplier" in carry:
                params["atr_sl_multiplier"] = carry["atr_sl_multiplier"]
            params["sma_fast_period"] = fast
            params["sma_slow_period"] = slow
            name = f"SMA {fast}/{slow}"
            configs.append((name, instrument, params, TF_5_15))

    results = run_phase("PHASE 3: SMA Period Sweep", configs, data_cache)

    best = best_from(results)
    if best:
        print(f"\n  >>> Phase 3 winner: {best['name']} on {best['instrument']} = {best['return_pct']:+.2f}%")
        carry["sma_fast_period"] = best["params"]["sma_fast_period"]
        carry["sma_slow_period"] = best["params"]["sma_slow_period"]
    return best or carry


# ── Phase 4: Timeframe Exploration ────────────────────────────────────────

def phase4_timeframes(data_cache: dict[str, pd.DataFrame], carry: dict) -> dict:
    """Sweep different timeframe combinations."""
    print("\n\n" + "#" * 70)
    print("# PHASE 4: Timeframe Exploration")
    print("#" * 70)

    timeframes = [
        ("1m/5m", ["1-MINUTE-LAST-EXTERNAL", "5-MINUTE-LAST-EXTERNAL"]),
        ("3m/10m", ["3-MINUTE-LAST-EXTERNAL", "10-MINUTE-LAST-EXTERNAL"]),
        ("3m/15m", ["3-MINUTE-LAST-EXTERNAL", "15-MINUTE-LAST-EXTERNAL"]),
        ("5m/15m", ["5-MINUTE-LAST-EXTERNAL", "15-MINUTE-LAST-EXTERNAL"]),
        ("5m/30m", ["5-MINUTE-LAST-EXTERNAL", "30-MINUTE-LAST-EXTERNAL"]),
        ("10m/30m", ["10-MINUTE-LAST-EXTERNAL", "30-MINUTE-LAST-EXTERNAL"]),
        ("15m/1h", ["15-MINUTE-LAST-EXTERNAL", "60-MINUTE-LAST-EXTERNAL"]),
        ("1h/2h", ["60-MINUTE-LAST-EXTERNAL", "120-MINUTE-LAST-EXTERNAL"]),
    ]

    configs = []
    for instrument in ["XAU/USD", "EUR/USD"]:
        for tf_name, tf_bars in timeframes:
            params = {**BASE_PARAMS}
            if "atr_tp_multiplier" in carry:
                params["atr_tp_multiplier"] = carry["atr_tp_multiplier"]
            if "atr_sl_multiplier" in carry:
                params["atr_sl_multiplier"] = carry["atr_sl_multiplier"]
            if "sma_fast_period" in carry:
                params["sma_fast_period"] = carry["sma_fast_period"]
            if "sma_slow_period" in carry:
                params["sma_slow_period"] = carry["sma_slow_period"]
            name = f"TF {tf_name}"
            configs.append((name, instrument, params, tf_bars))

    results = run_phase("PHASE 4: Timeframe Exploration", configs, data_cache)

    best = best_from(results)
    if best:
        print(f"\n  >>> Phase 4 winner: {best['name']} on {best['instrument']} = {best['return_pct']:+.2f}%")
        carry["best_timeframes"] = best["extra_bar_types"]
    return best or carry


# ── Phase 5: RSI & MACD Tuning ────────────────────────────────────────────

def phase5_rsi_macd(data_cache: dict[str, pd.DataFrame], carry: dict) -> dict:
    """Tune RSI threshold and MACD periods."""
    print("\n\n" + "#" * 70)
    print("# PHASE 5: RSI & MACD Tuning")
    print("#" * 70)

    best_tf = carry.get("best_timeframes", TF_5_15)

    # Build base from carry-forward
    base = {**BASE_PARAMS}
    for key in ["atr_tp_multiplier", "atr_sl_multiplier", "sma_fast_period", "sma_slow_period"]:
        if key in carry:
            base[key] = carry[key]

    configs = []

    # RSI threshold sweep
    rsi_thresholds = [0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65]
    for instrument in ["XAU/USD", "EUR/USD"]:
        for rsi in rsi_thresholds:
            params = {**base, "rsi_level_threshold": rsi}
            name = f"RSI={rsi}"
            configs.append((name, instrument, params, best_tf))

    # MACD period sweep
    macd_configs = [
        (8, 17, 9, "MACD 8/17/9 fast"),
        (10, 20, 5, "MACD 10/20/5 fast-sig"),
        (10, 26, 9, "MACD 10/26/9"),
        (12, 26, 9, "MACD 12/26/9 std"),
        (12, 26, 5, "MACD 12/26/5 fast-sig"),
        (16, 36, 9, "MACD 16/36/9 slow"),
        (8, 21, 5, "MACD 8/21/5 ultra"),
    ]
    for instrument in ["XAU/USD", "EUR/USD"]:
        for fast, slow, sig, name in macd_configs:
            params = {
                **base,
                "macd_fast_period": fast,
                "macd_slow_period": slow,
                "macd_signal_period": sig,
            }
            configs.append((name, instrument, params, best_tf))

    results = run_phase("PHASE 5: RSI & MACD Tuning", configs, data_cache)

    best = best_from(results)
    if best:
        print(f"\n  >>> Phase 5 winner: {best['name']} on {best['instrument']} = {best['return_pct']:+.2f}%")
        if "rsi_level_threshold" in best["params"]:
            carry["rsi_level_threshold"] = best["params"]["rsi_level_threshold"]
        for key in ["macd_fast_period", "macd_slow_period", "macd_signal_period"]:
            if key in best["params"]:
                carry[key] = best["params"][key]
    return best or carry


# ── Phase 6: Session Hours + Cross-Instrument ─────────────────────────────

def phase6_cross_instrument(data_cache: dict[str, pd.DataFrame], carry: dict) -> dict:
    """Validate winning config across instruments and session hours."""
    print("\n\n" + "#" * 70)
    print("# PHASE 6: Cross-Instrument Validation + Session Hours")
    print("#" * 70)

    best_tf = carry.get("best_timeframes", TF_5_15)

    # Build best params from all carry-forward
    best_params = {**BASE_PARAMS}
    for key in [
        "atr_tp_multiplier", "atr_sl_multiplier",
        "sma_fast_period", "sma_slow_period",
        "rsi_level_threshold",
        "macd_fast_period", "macd_slow_period", "macd_signal_period",
    ]:
        if key in carry:
            best_params[key] = carry[key]

    sessions = [
        ("24h", 0, 24),
        ("skip-dead 2-22", 2, 22),
        ("London+ 6-20", 6, 20),
        ("London/NY 8-17", 8, 17),
    ]

    configs = []
    for instrument in ["XAU/USD", "EUR/USD", "GBP/USD", "AUD/USD", "NZD/USD"]:
        for sess_name, start_h, end_h in sessions:
            params = {**best_params, "session_start_hour": start_h, "session_end_hour": end_h}
            name = f"{sess_name}"
            configs.append((name, instrument, params, best_tf))

    results = run_phase("PHASE 6: Cross-Instrument + Sessions", configs, data_cache)

    best = best_from(results)
    if best:
        print(f"\n  >>> Phase 6 winner: {best['name']} on {best['instrument']} = {best['return_pct']:+.2f}%")
        carry["session_start_hour"] = best["params"]["session_start_hour"]
        carry["session_end_hour"] = best["params"]["session_end_hour"]
    return best or carry


# ── Phase 7: Final Assembly ───────────────────────────────────────────────

def phase7_final(data_cache: dict[str, pd.DataFrame], carry: dict) -> None:
    """Run final best config on all instruments and print comprehensive report."""
    print("\n\n" + "#" * 70)
    print("# PHASE 7: Final Assembly — The Money Maker Config")
    print("#" * 70)

    best_tf = carry.get("best_timeframes", TF_5_15)

    # Build final params
    final_params = {**BASE_PARAMS}
    for key in [
        "atr_tp_multiplier", "atr_sl_multiplier",
        "sma_fast_period", "sma_slow_period",
        "rsi_level_threshold",
        "macd_fast_period", "macd_slow_period", "macd_signal_period",
        "session_start_hour", "session_end_hour",
    ]:
        if key in carry:
            final_params[key] = carry[key]

    print("\n  Final configuration:")
    for k, v in sorted(final_params.items()):
        print(f"    {k}: {v}")
    print(f"    timeframes: {best_tf}")
    print(f"    balance: ${BALANCE:,.0f}")
    print(f"    leverage: {LEVERAGE}x")

    configs = []
    for instrument in ["XAU/USD", "EUR/USD", "GBP/USD"]:
        name = "FINAL"
        configs.append((name, instrument, dict(final_params), best_tf))

    results = run_phase("PHASE 7: FINAL MONEY MAKER RESULTS", configs, data_cache)

    # Print CLI commands for reproducibility
    print("\n\n" + "=" * 70)
    print("REPRODUCIBLE CLI COMMANDS")
    print("=" * 70)

    for instrument in ["XAU/USD", "EUR/USD", "GBP/USD"]:
        inst_info = INSTRUMENTS[instrument]
        inst_slug = instrument.replace("/", "")
        data_file = inst_info["file"]
        param_str = " ".join(f"-p {k}={v}" for k, v in sorted(final_params.items()))
        extra_str = " ".join(f"--extra-bar-type {bt}" for bt in best_tf)
        print(f"\n# {instrument}")
        print(
            f"uv run pyfx backtest -s coban_reborn -i {instrument} "
            f"--data-file {data_file} --start 2025-01-01 --end 2026-03-31 "
            f"--balance {BALANCE} --leverage {LEVERAGE} "
            f"--trade-size {inst_info['trade_size']} "
            f"{extra_str} {param_str} --save"
        )


# ── Hall of Fame ──────────────────────────────────────────────────────────

def print_hall_of_fame() -> None:
    """Print top 20 results across all phases."""
    print("\n\n" + "=" * 130)
    print("HALL OF FAME — Top 20 Configurations Across All Phases")
    print("=" * 130)

    valid = [r for r in HALL_OF_FAME if r["trades"] >= 20 and r.get("profit_factor") is not None]
    top = sorted(valid, key=lambda r: r["return_pct"], reverse=True)[:20]

    header = (
        f"{'#':>3} {'Name':<30} {'Inst':<8} {'Trades':>6} {'WinR%':>6} {'P&L':>12} "
        f"{'Ret%':>9} {'MaxDD%':>7} {'PF':>6}"
    )
    print(header)
    print("-" * 100)

    for i, r in enumerate(top, 1):
        pf = f"{r['profit_factor']:.2f}" if r.get("profit_factor") is not None else "N/A"
        print(
            f"{i:>3} {r['name']:<30} {r.get('instrument', '?'):<8} {r['trades']:>6} {r['win_rate']:>5.1f}% "
            f"{r['pnl']:>+12.2f} {r['return_pct']:>+8.2f}% "
            f"{r['max_dd']:>6.2f}% {pf:>6}"
        )

    print("=" * 130)

    if top:
        print(f"\n  CHAMPION: {top[0]['name']} on {top[0].get('instrument', '?')}")
        print(f"  Return: {top[0]['return_pct']:+.2f}%  |  P&L: {top[0]['pnl']:+,.2f}  |  PF: {top[0].get('profit_factor', 0):.2f}  |  MaxDD: {top[0]['max_dd']:.2f}%")
        print(f"  Params: {top[0].get('params', {})}")
        print(f"  Timeframes: {top[0].get('extra_bar_types', [])}")


# ── Main ──────────────────────────────────────────────────────────────────

def main() -> None:
    t_start = time.monotonic()
    data_cache: dict[str, pd.DataFrame] = {}
    carry: dict = {}  # Carry-forward best params between phases

    print("=" * 70)
    print("MONEY MAKER SWEEP")
    print(f"Balance: ${BALANCE:,.0f}  |  Leverage: {LEVERAGE}x  |  Period: {START.date()} to {END.date()}")
    print("=" * 70)

    # Phase 1
    p1_best = phase1_baseline(data_cache)
    if p1_best:
        carry.update({k: v for k, v in p1_best.get("params", {}).items() if k in BASE_PARAMS})

    # Phase 2
    phase2_atr_grid(data_cache, carry)

    # Phase 3
    phase3_sma_sweep(data_cache, carry)

    # Phase 4
    phase4_timeframes(data_cache, carry)

    # Phase 5
    phase5_rsi_macd(data_cache, carry)

    # Phase 6
    phase6_cross_instrument(data_cache, carry)

    # Phase 7
    phase7_final(data_cache, carry)

    # Hall of Fame
    print_hall_of_fame()

    elapsed = time.monotonic() - t_start
    print(f"\n\nTotal sweep time: {elapsed:.1f}s ({elapsed/60:.1f} min)")


if __name__ == "__main__":
    main()
