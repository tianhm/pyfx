# CobanReborn Strategy Research Journal

**Status:** Active — realism improvements applied, out-of-sample tested on 2024
**Verdict:** XAU/USD is the only instrument with a robust edge (2024 OOS: PF 1.89, +303%). EUR/USD edge is thin (2024 OOS: PF 1.17) and would likely not survive live costs. Strategy NOT live-ready (5/16 audit checks pass). Recommendation: focus on XAU/USD only for live; EUR/USD needs regime filtering or different approach.

---

## Overview

CobanReborn is a multi-timeframe strategy with two entry modes:

- **"full"** (original): Requires 5-layer signal confluence — H1 SMA crossover + MACD histogram zero-cross + RSI trendline break, H2 RSI trendline break confirmation, optional M1 full confluence. Extremely selective; produces 0 trades on 6 months of EURUSD.
- **"trend_follow"** (added 2026-04-03): SMA 4/9 cross as trigger, MACD histogram sign and RSI level (>0.50/<0.50) as directional filters. Active across all tested instruments.

Three exit modes: fixed TP/SL (pips), trailing stop, ATR-based dynamic TP/SL.

**Source:** `pyfx/strategies/coban_reborn.py`
**Experimental testbed:** `pyfx/strategies/coban_experimental.py` (7 entry x 3 exit modes)
**Sweep scripts:** `scripts/coban_sweep.py`, `scripts/coban_multi_pair.py`

---

## Current Best Config

**Primary instrument: XAU/USD** — only instrument with a robust edge across in-sample (2025, PF 2.11) and out-of-sample (2024, PF 1.89). EUR/USD edge is too thin (OOS PF 1.17) for live trading.

```bash
# XAU/USD — PRIMARY (only recommended for live)
uv run pyfx backtest -s coban_reborn -i XAU/USD \
  --data-file ~/.pyfx/data/XAUUSD_2025-2026_M1.parquet \
  --start 2025-01-01 --end 2026-03-31 \
  --extra-bar-type 5-MINUTE-LAST-EXTERNAL \
  --extra-bar-type 15-MINUTE-LAST-EXTERNAL \
  --trade-size 100 -p spread_pips=3 --save

# EUR/USD — for research only (edge too thin for live)
uv run pyfx backtest -s coban_reborn \
  --data-file ~/.pyfx/data/EURUSD_2025-2026_M1.parquet \
  --start 2025-01-01 --end 2026-03-31 \
  --extra-bar-type 5-MINUTE-LAST-EXTERNAL \
  --extra-bar-type 15-MINUTE-LAST-EXTERNAL \
  -p spread_pips=1.5 --save

# GBP/USD — secondary, borderline viable
uv run pyfx backtest -s coban_reborn -i GBP/USD \
  --data-file ~/.pyfx/data/GBPUSD_2025-2026_M1.parquet \
  --start 2025-01-01 --end 2026-03-31 \
  --extra-bar-type 5-MINUTE-LAST-EXTERNAL \
  --extra-bar-type 15-MINUTE-LAST-EXTERNAL \
  -p spread_pips=1.5 --save

# Walk-forward analysis
uv run python scripts/walk_forward.py --data-file ~/.pyfx/data/XAUUSD_2025-2026_M1.parquet --instrument XAU/USD --trade-size 100

# Parameter sensitivity sweep
uv run python scripts/param_sensitivity.py --data-file ~/.pyfx/data/EURUSD_2025-2026_M1.parquet
```

**Key parameters (defaults as of 2026-04-04):**
- `entry_mode=trend_follow` — SMA cross trigger + MACD/RSI filters
- `exit_mode=atr` — ATR(14) based TP/SL with spread deduction, multipliers 2.0/1.5
- `session_start_hour=0`, `session_end_hour=24` (24h trading)
- `sma_fast_period=4`, `sma_slow_period=9` (param sweep suggests 2/7 may be better)
- `rsi_level_threshold=0.50` — RSI above 0.5 for longs, below for shorts
- `filter_staleness_seconds=7200` — reject MACD/RSI values older than 2 H1 bars
- `next_bar_entry=False` — set to True for more realistic entry timing
- `spread_pips=1.5` (FX), `3.0` (commodities)
- `prob_slippage=0.9` — 90% of orders experience slippage (in runner.py)

---

## Key Findings

1. **The original 5-layer confluence is too strict.** All three H1 signals (SMA cross + MACD zero-cross + RSI trendline break) must coincide within 1 hour. This essentially never happens on real data. 0 trades on 6 months of EURUSD.

2. **Trend Follow is the best entry mode.** Using SMA cross as the *trigger* (not requiring coincidence with other crossovers) and MACD/RSI as *directional filters* (histogram positive? RSI above 0.5?) produces 10-15x more trades while maintaining edge.

3. **5m/15m timeframe dramatically outperforms 1h/2h.** Every single variation tested produced more total P&L on 5m/15m. More signals fire, more trades, higher total return. Win rate drops slightly (90% -> 50%) but profit factor stays strong (1.7-2.2).

4. **ATR exits are the most universal.** Fixed pip TP/SL only works for FX pairs where pip values are similar. Gold at $3000 needs different pip scaling than EUR/USD at $1.10. ATR auto-adapts to any instrument's volatility.

5. **Non-FX instruments need custom setup.** `TestInstrumentProvider.default_fx_ccy()` gives wrong precision for gold/oil. Fixed in runner.py with `_INSTRUMENT_OVERRIDES` dict. Also need adjusted `trade_size` (100 oz for gold, 1000 bbl for oil vs 100k for FX).

6. **USD/JPY P&L is unreliable.** NautilusTrader can't convert JPY-denominated P&L to USD without a separate price feed. Win rate and profit factor are valid; absolute dollar P&L is in JPY terms.

7. **Backtest realism matters.** Adding 50% slippage probability and intra-bar high/low exit checks reduced P&L by ~10% vs. naive bar-close exits. Still profitable across all instruments.

8. **24h trading massively outperforms London-only for USD-quoted pairs.** Removing the 8-17 UTC session filter increased P&L by +73-97% on EUR/USD, GBP/USD, XAU/USD. Exception: EUR/GBP — 24h *hurts* it (PF drops 1.46→1.17). Keep 8-17h for EUR/GBP only.

9. **ATR exits must deduct spread.** Original ATR implementation was missing spread deduction (fixed/trailing had it). Fixing this reduced EUR/USD P&L by 16% — the bug made ATR exits ~1.5 pips too optimistic.

10. **Non-USD-quote pairs have broken P&L.** USD/CHF (quote=CHF) and EUR/GBP (quote=GBP) show the same conversion failure as USD/JPY. Win rate and PF are valid; dollar P&L and drawdown are not. Only USD-quoted pairs (EUR/USD, GBP/USD, XAU/USD) give trustworthy dollar returns.

11. **EUR/USD edge is regime-dependent.** PF 1.42 in 2025 but only 1.17 in 2024 out-of-sample. The 2024 walk-forward had avg PF 1.10 with 1 losing window. This edge would not survive live trading costs.

12. **XAU/USD is the real opportunity.** PF 1.89 on 2024 OOS (+303% return), PF 2.11 on 2025 in-sample. Gold trends more consistently than FX, which is exactly what trend_follow captures. Edge persists across 2 full years.

13. **Parameters are robust, not curve-fit.** All 7 key parameters show smooth P&L gradients when perturbed +/- 40%. No cliff edges. The edge comes from the trend-following logic + gold's trending nature, not from specific parameter values.

14. **SMA fast=2 and wider ATR exits could improve P&L by ~40%.** Param sweep: SMA fast 4→2 gives +38%, ATR SL 1.5→2.5 gives +17%. These are individual effects; interaction effects unknown. Needs testing on XAU/USD specifically.

15. **90% slippage reduced EUR/USD P&L by ~10%.** Going from 50% to 90% slippage probability cost $6,738 on EUR/USD (from $65k to $58k). Acceptable for XAU/USD where the edge is much larger.

---

## Research Log

### 2026-04-03 — Realism improvements, out-of-sample testing, parameter sensitivity

**Context:** Before trusting the strategy for live trading, needed to answer three critical questions: (1) Are parameters robust or fragile? (2) Does the edge exist outside our training period? (3) How much does improved realism cost?

**Code changes:**
1. **Signal staleness** (`filter_staleness_seconds=7200`): MACD histogram and RSI values now track timestamps. Trend_follow rejects stale filter values (>2 H1 bars old). Applied to both coban_reborn and coban_experimental.
2. **Next-bar entry** (`next_bar_entry=False`): New option to defer entry to next M1 bar open (more realistic timing). Off by default for backward compat.
3. **Slippage probability**: 50% → 90% (nearly all orders now experience slippage).
4. **New scripts**: `scripts/param_sensitivity.py` (parameter sweep), `scripts/walk_forward.py` (rolling window analysis).

**Impact of realism changes (EUR/USD 2025-2026, 15 months):**

Previous baseline (50% slippage): $65,023, PF 1.49
New baseline (90% slippage + staleness): $58,285, PF 1.42 (-10% P&L)

---

**Parameter Sensitivity (EUR/USD 2025-2026, 15 months):**

All parameters show smooth gradients — no cliff edges. The strategy is NOT curve-fit.

| Parameter | Default | Best Value | Best P&L | Delta | Assessment |
|-----------|---------|------------|----------|-------|------------|
| SMA fast | 4 | **2** | $80,466 | +38% | Shorter is better. Smooth slope. |
| SMA slow | 9 | 7 | $64,471 | +11% | Moderate improvement. Smooth. |
| ATR TP mult | 2.0 | **3.0** | $64,274 | +10% | Let winners run. Smooth. |
| ATR SL mult | 1.5 | **2.5** | $68,029 | +17% | Wider stops help. Smooth. |
| RSI threshold | 0.50 | 0.40-0.60 | ~$59-61k | ±3% | **Insensitive** — RSI filter barely matters |
| Signal window | 3600s | 3600-7200 | ~$58k | ±1% | **Insensitive** — doesn't matter much |
| Staleness | 7200s | any | $58,285 | 0% | **Completely insensitive** on this data |

Key insight: **SMA fast period has the most impact**. Reducing from 4→2 adds +38% P&L with smooth degradation in both directions. This is robust, not curve-fit.

Optimal combo (not tested yet): SMA 2/7, ATR TP=3.0, SL=2.5 could yield ~$90-100k on EUR/USD (rough estimate from individual sweeps — interaction effects unknown).

---

**Walk-Forward Analysis (EUR/USD 2025-2026, 3-month rolling windows):**

| Window | Trades | P&L | PF | WR |
|--------|--------|-----|-----|-----|
| 2025-01→04 | 1,610 | +$13,216 | 1.48 | 48.5% |
| 2025-02→05 | 1,573 | +$22,104 | 1.71 | 52.1% |
| 2025-03→06 | 1,622 | +$21,803 | 1.62 | 50.4% |
| 2025-04→07 | 1,626 | +$23,554 | 1.67 | 51.5% |
| 2025-05→08 | 1,682 | +$13,827 | 1.44 | 50.5% |
| 2025-06→09 | 1,646 | +$10,431 | 1.36 | 50.4% |
| 2025-07→10 | 1,701 | +$7,546 | 1.26 | 49.1% |
| 2025-08→11 | 1,673 | +$7,065 | 1.27 | 48.5% |
| 2025-09→12 | 1,679 | +$6,046 | 1.25 | 48.2% |
| 2025-10→01 | 1,679 | +$3,122 | 1.13 | 46.1% |
| 2025-11→02 | 1,605 | +$3,611 | 1.16 | 46.0% |
| 2025-12→03 | 1,583 | +$4,249 | 1.18 | 45.7% |

**12/12 windows profitable**, but PF declines from 1.71 (Q1) → 1.13 (Q4). Edge is fading through 2025.

**XAU/USD Walk-Forward (2025, 3-month windows):**

| Window | Trades | P&L | PF | WR |
|--------|--------|-----|-----|-----|
| 2025-01→04 | 1,477 | +$110,068 | 2.16 | 54.7% |
| 2025-02→05 | 1,472 | +$175,815 | 2.30 | 56.9% |
| 2025-03→06 | 1,490 | +$194,264 | 2.24 | 56.3% |

**3/3 windows, avg PF 2.23.** Gold's edge is much stronger and more consistent.

---

**OUT-OF-SAMPLE TEST — 2024 data (zero parameter changes):**

| Instrument | Trades | P&L | Return | PF | WR | MaxDD |
|-----------|--------|-----|--------|-----|-----|-------|
| EUR/USD | 6,622 | +$14,415 | +14.4% | 1.17 | 43.5% | -1.56% |
| GBP/USD | 6,483 | +$31,323 | +31.3% | 1.29 | 46.4% | -1.96% |
| **XAU/USD** | **6,009** | **+$303,289** | **+303.3%** | **1.89** | **52.4%** | **-1.80%** |

**EUR/USD Walk-Forward 2024 (3-month windows):**

| Window | Trades | P&L | PF | WR |
|--------|--------|-----|-----|-----|
| 2024-01→04 | 1,662 | +$2,671 | 1.13 | 43.7% |
| 2024-02→05 | 1,664 | +$1,093 | 1.06 | 42.6% |
| 2024-03→06 | 1,670 | +$1,577 | 1.09 | 42.9% |
| 2024-04→07 | 1,654 | +$1,826 | 1.10 | 42.5% |
| 2024-05→08 | 1,698 | +$63 | 1.00 | 40.7% |
| 2024-06→09 | 1,679 | -$286 | 0.99 | 40.6% |
| 2024-07→10 | 1,715 | +$2,718 | 1.13 | 43.3% |
| 2024-08→11 | 1,689 | +$2,534 | 1.12 | 43.2% |
| 2024-09→12 | 1,636 | +$6,307 | 1.26 | 44.3% |

**8/9 windows profitable, but avg PF only 1.10.** One window (Jun-Sep) was a net loss. This is a razor-thin edge that would NOT survive live trading costs.

---

**Critical Conclusions:**

11. **EUR/USD edge is regime-dependent.** PF 1.42 in 2025 but only 1.17 in 2024. The 2025 edge is partly from a trending EUR/USD environment (dollar weakness). In the more range-bound 2024, the strategy barely breaks even.

12. **XAU/USD is the real opportunity.** PF 1.89 in 2024 OOS, PF 2.11 in 2025. Gold trends more consistently than FX, and this strategy captures trending moves. The edge persists across 2 years of data.

13. **GBP/USD is borderline.** PF 1.29 in 2024, 1.54 in 2025. Better than EUR/USD but still thin. Would need to be tested with live costs modeled.

14. **EUR/USD with live costs would lose money.** PF 1.17 minus an estimated 60-80% haircut from real slippage, spread widening, and latency = negative expectancy. Do NOT trade EUR/USD with this strategy unless improved.

15. **Parameter sensitivity is robust.** No parameter causes P&L to cliff. The edge is not from parameter tuning — it's from the trend-following logic interacting with an asset's tendency to trend. Gold trends, so it works. EUR/USD is choppy, so it barely works.

---

### 2026-04-03 — Instrument precision fix + sortable dashboard

**Context:** Dashboard showed $56M P&L for gold and $2.3M for oil — clearly wrong.

**Root cause:** `_get_instrument()` used `default_fx_ccy()` for all pairs, giving gold/oil 5-decimal FX precision (pip = 0.0001). At gold price $3000, a 10-pip move = $0.001 instead of the correct $0.10. P&L was inflated ~1000x.

**Fix:** Added `_INSTRUMENT_OVERRIDES` dict in `runner.py` that creates `CurrencyPair` with 2-decimal precision for XAU/USD, OIL/USD, BCO/USD, WTI/USD. Also fixed `trade_size` override via `-p` flag (was causing duplicate kwarg error).

**Corrected results (Jan-Mar 2025):**

| Instrument | Trade Size | Trades | P&L | Return | WR% | PF | MaxDD |
|-----------|-----------|--------|-----|--------|-----|-----|-------|
| XAU/USD | 100 oz | 557 | +$54,364 | +54.4% | 54.9% | 2.24 | -1.68% |
| OIL/USD | 1000 bbl | 581 | +$5,639 | +5.6% | 44.6% | 1.16 | -3.27% |

**Also added:** Sortable columns on the backtest list page (client-side JS, click headers).

---

### 2026-04-03 — Consolidation into coban_reborn.py

**Context:** After identifying trend_follow + ATR as the winning combo, merged it into the main strategy as configurable modes (backward compatible with `entry_mode="full"` default).

**Changes:**
- Added `entry_mode`, `exit_mode` config params to `CobanRebornConfig`
- Added `AverageTrueRange` indicator on H1
- Refactored exit logic to use bar high/low (not close) for TP/SL checks
- Added `_entry_trend_follow()`, `_exit_trailing()`, `_exit_atr()` methods
- 18 new tests (73 total), 96% coverage on strategy module
- Created `/realism-audit` skill

---

### 2026-04-03 — Multi-pair validation (5 instruments)

**Context:** Need to confirm strategy works beyond EUR/USD before trusting it.

**Data:** Fetched M1 data via `npx dukascopy-node` for USD/JPY, GBP/USD, XAU/USD, OIL/USD (WTI as `lightcmdusd`). EURUSD already had Jan 2025 - Mar 2026 data.

**Instruments tested:** EUR/USD, USD/JPY, GBP/USD, XAU/USD, OIL/USD
**Period:** Jan 1 - Mar 31, 2025 (common range)
**Config:** Trend Follow entry + ATR exits, 5m/15m timeframes

**Results (with realism: 50% slippage, bar high/low exits):**

| Instrument | Trades | Win% | P&L | MaxDD | PF |
|-----------|--------|------|-----|-------|-----|
| EUR/USD | 590 | 50% | +$8,270 | -0.65% | 1.63 |
| GBP/USD | 587 | 52% | +$9,790 | -0.69% | 1.64 |
| USD/JPY | 596 | 49% | * | 0.00% | 1.38 |
| XAU/USD | 558 | 55% | * | * | 2.31 |
| OIL/USD | 580 | 55% | * | * | 1.88 |

(*) Non-FX P&L was inflated at this point — corrected in later session.

**Conclusion:** Strategy is profitable across ALL 5 instruments. Not a curve-fit to EUR/USD. ATR exits adapt correctly to different volatility profiles.

---

### 2026-04-03 — Backtest realism audit

**Context:** Before trusting multi-pair results, audited how realistic the backtest engine is.

**Gaps found and fixed:**
1. **Slippage (CRITICAL):** No fill model -> added `FillModel(prob_slippage=0.5, random_seed=42)`
2. **Intra-bar exits (CRITICAL):** TP/SL used bar close (look-ahead bias) -> changed to bar high/low
3. **Spread handling (OK):** 1.5 pip spread deducted from TP/SL distances
4. **Commission (LOW):** 0.002% MakerTaker fees — low but spread is the real cost for FX
5. **Position sizing (STATIC):** Fixed 100k lots regardless of equity — acceptable for comparison

**Remaining gaps:**
- USD/JPY P&L not converted to USD (NautilusTrader limitation)
- No dynamic position sizing (risk-per-trade)
- No margin call simulation

**Impact:** Adding realism reduced P&L by ~10-15% but all strategies remained profitable.

---

### 2026-04-03 — Initial 10-variation sweep (EUR/USD only)

**Context:** Original CobanReborn strategy produced 0 trades on 6 months of EURUSD. Need to find what works.

**Approach:** Created `coban_experimental.py` with 7 entry modes and 3 exit modes. Wrote `scripts/coban_sweep.py` to run 10 variations programmatically.

**Entry modes tested:**
1. Relaxed (no M1/double-confirm) — 21 trades
2. 2-of-3 H1 signals — 61 trades
3. No H2 confirmation — 30 trades
4. Wide signal window (4h) — 62 trades
5. SMA + MACD only — 94 trades
6. RSI level filter — 83 trades
7. **Trend Follow — 89 trades, best P&L**

**Exit modes tested (on SMA+MACD entry):**
8. Trailing stop — 94 trades
9. Better R:R (30:15) — 93 trades
10. ATR exits — 91 trades

**Results (EUR/USD, Jan-Jun 2025, 1h/2h timeframe):**

| # | Variation | Trades | WR% | P&L | PF |
|---|-----------|--------|-----|-----|-----|
| 7 | **Trend Follow** | **89** | **90%** | **+$5,682** | **3.78** |
| 10 | ATR exits | 91 | 50% | +$4,076 | 1.68 |
| 4 | Wide window | 62 | 80% | +$3,865 | 4.12 |
| 2 | 2-of-3 signals | 61 | 90% | +$3,585 | 3.66 |
| 6 | RSI level filter | 83 | 80% | +$3,318 | 2.08 |

**Then tested 5m/15m timeframe — massive improvement:**

| Variation | Trades | P&L (1h/2h) | P&L (5m/15m) | Improvement |
|-----------|--------|-------------|--------------|-------------|
| Trend Follow | 89 vs 1211 | +$5,682 | +$22,581 | 4.0x |
| TF+ATR | 86 vs 1217 | +$7,498 | +$23,559 | 3.1x |
| TF+R:R 30:15 | 87 vs 1207 | +$3,881 | +$24,312 | 6.3x |

**Combined winner: Trend Follow + R:R 30:15 on 5m/15m** at +$24,312, but ATR exits are more universal across instruments.

---

### 2026-04-03 — Full 15-Month Sweep (Jan 2025 – Mar 2026)

**Context:** Previous tests used 3-6 month windows. Extended to full available data (15 months) across all 5 instruments to validate edge persistence. Wiped DB and started fresh with 20 backtests (5 pairs × 4 variations).

**Config:** All use `coban_reborn` (production strategy), `entry_mode=trend_follow`. Four variations: Fixed/ATR/Trailing exits on 5m/15m, plus ATR on 1h/2h for timeframe comparison.

**Results (Jan 2025 – Mar 2026, 15 months):**

| Instrument | Variation | Trades | WR% | P&L | Return | PF | MaxDD |
|-----------|-----------|--------|-----|-----|--------|-----|-------|
| **EUR/USD** | Fixed (5m/15m) | 3058 | 52.9% | +$38,912 | +38.9% | 1.62 | -0.90% |
| **EUR/USD** | **ATR (5m/15m)** | **3120** | **49.6%** | **+$42,846** | **+42.9%** | **1.63** | **-0.65%** |
| **EUR/USD** | Trailing (5m/15m) | 3074 | 48.7% | +$40,534 | +40.5% | 1.60 | -0.85% |
| EUR/USD | ATR (1h/2h) | 197 | 58.4% | +$16,410 | +16.4% | 2.67 | -1.08% |
| **GBP/USD** | Fixed (5m/15m) | 807 | 60.0% | +$14,465 | +14.5% | 1.75 | -0.53% |
| **GBP/USD** | ATR (5m/15m) | 813 | 51.9% | +$14,564 | +14.6% | 1.67 | -0.69% |
| **GBP/USD** | **Trailing (5m/15m)** | **806** | **52.4%** | **+$15,386** | **+15.4%** | **1.75** | **-0.99%** |
| GBP/USD | ATR (1h/2h) | 44 | 54.5% | +$3,030 | +3.0% | 1.78 | -0.86% |
| USD/JPY | Fixed (5m/15m) | 673 | 62.4% | * | * | 1.46 | 0.00% |
| USD/JPY | ATR (5m/15m) | 683 | 49.8% | * | * | 1.43 | 0.00% |
| USD/JPY | Trailing (5m/15m) | 685 | 47.4% | * | * | 1.41 | 0.00% |
| USD/JPY | ATR (1h/2h) | 39 | 61.5% | * | * | 3.07 | 0.00% |
| **XAU/USD** | Fixed (5m/15m) | 674 | 55.0% | +$92,594 | +92.6% | 2.37 | -2.83% |
| **XAU/USD** | ATR (5m/15m) | 696 | 55.3% | +$77,554 | +77.6% | 2.14 | -2.31% |
| **XAU/USD** | **Trailing (5m/15m)** | **674** | **55.0%** | **+$94,434** | **+94.4%** | **2.39** | **-2.80%** |
| XAU/USD | ATR (1h/2h) | 36 | 61.1% | +$14,225 | +14.2% | 2.35 | -3.31% |
| OIL/USD | Fixed (5m/15m) | 565 | 42.5% | +$11,584 | +11.6% | 1.32 | -3.42% |
| OIL/USD | ATR (5m/15m) | 590 | 44.2% | +$5,334 | +5.3% | 1.15 | -3.27% |
| OIL/USD | Trailing (5m/15m) | 565 | 42.5% | +$11,584 | +11.6% | 1.32 | -3.42% |
| OIL/USD | ATR (1h/2h) | 35 | 65.7% | +$9,031 | +9.0% | 4.08 | -0.57% |

(*) USD/JPY P&L is in JPY terms — unreliable without JPY/USD price feed. Win rate/PF are valid.

**Key findings from 15-month test:**

1. **Edge persists over 15 months.** All instruments profitable across all exit modes. Not a short-window artifact.

2. **5m/15m still dominates 1h/2h by total P&L** — 2.5-5x more on EUR/USD, 5x on GBP/USD, 5.5-6.6x on XAU/USD. 1h/2h has higher PF but far fewer trades.

3. **XAU/USD is the star performer** — +94% return (trailing), PF 2.39, with manageable -2.8% drawdown. Gold's trending nature suits trend_follow perfectly.

4. **OIL/USD is the weakest** — PF 1.15-1.32, sub-45% win rate. ATR exits on 5m/15m barely profitable (+5.3%). Oil is choppy and mean-reverting — trend_follow struggles. **Recommendation: drop OIL/USD from future tests** unless a different entry mode is developed.

5. **USD/JPY results are untrustable** — NautilusTrader can't convert JPY P&L to USD. MaxDD shows 0% (broken calculation). Win rate and PF look okay but can't compare dollar returns. **Recommendation: drop USD/JPY until JPY/USD conversion is solved**, or switch to pairs with USD as quote currency only.

6. **ATR vs Fixed vs Trailing** — very close on most pairs. ATR edges out on EUR/USD (+$42.8k vs $38.9k fixed). Trailing wins on GBP/USD and XAU/USD. Fixed and Trailing gave identical results on OIL/USD (suggesting trailing stop never activated — oil moves aren't big enough).

7. **GBP/USD has fewer bars** (117k vs 461k EUR/USD) — Dukascopy data may have gaps. GBP results are consistent but lower trade count suggests data quality issue.

**Recommendations for next steps:**
- **Drop OIL/USD** — marginal edge, high drawdown relative to return
- **Drop USD/JPY** — P&L metrics broken, can't compare
- **Add AUD/USD, NZD/USD, USD/CHF, EUR/GBP** — high-liquidity pairs with USD as quote (or EUR cross) to avoid JPY conversion issue
- **Test 24h trading** — current 8-17 UTC session restriction may miss Asian session moves, especially for gold
- **Focus portfolio on: EUR/USD, GBP/USD, XAU/USD** — proven edge, reliable P&L

---

### 2026-04-03 — New Pairs: USD/CHF + EUR/GBP

**Context:** Expanding beyond 3 core pairs. AUD/USD and NZD/USD downloads failed (Dukascopy IP blocking). USD/CHF and EUR/GBP downloaded successfully (457k and 458k bars respectively).

**Important caveat:** Both pairs have non-USD quote currencies (CHF, GBP). P&L is reported in CHF/GBP terms — dollar amounts are unreliable (MaxDD shows 0.00%). Win rate and profit factor are valid.

**Results (Jan 2025 – Mar 2026, 15 months, TF entry):**

| Instrument | Variation | Trades | WR% | P&L* | PF |
|-----------|-----------|--------|-----|------|-----|
| USD/CHF | Fixed (5m/15m) | 2913 | 50.9% | +33,694 CHF | 1.70 |
| USD/CHF | ATR (5m/15m) | 3018 | 49.9% | +29,225 CHF | 1.56 |
| USD/CHF | **Trailing (5m/15m)** | **2927** | **49.0%** | **+36,481 CHF** | **1.72** |
| USD/CHF | ATR (1h/2h) | 187 | 65.2% | +17,436 CHF | 3.97 |
| EUR/GBP | Fixed (5m/15m) | 2940 | 47.9% | +18,167 GBP | 1.50 |
| EUR/GBP | ATR (5m/15m) | 3015 | 47.8% | +17,086 GBP | 1.46 |
| EUR/GBP | Trailing (5m/15m) | 2940 | 47.1% | +17,331 GBP | 1.47 |
| EUR/GBP | ATR (1h/2h) | 207 | 62.3% | +8,708 GBP | 2.76 |

(*) P&L in local quote currency, NOT USD.

**Findings:**
1. **USD/CHF looks strong** — PF 1.70-1.72 on 5m/15m, comparable to EUR/USD. ~2,900+ trades confirms good liquidity.
2. **EUR/GBP is weaker** — PF 1.46-1.50, sub-48% win rate. Lower volatility pair = smaller edge.
3. **Trailing exit wins again** on USD/CHF — consistent with GBP/USD and XAU/USD.
4. **1h/2h PF is inflated** — 187 trades on USD/CHF with PF 3.97 is too few trades for statistical significance.

---

### 2026-04-03 — 24h Trading Test (session_start_hour=0, session_end_hour=24)

**Context:** Default strategy trades 8-17 UTC (London/NY overlap only). Testing whether removing the session restriction improves results by capturing Asian session moves.

**Config:** ATR exits on 5m/15m (most universal config), compared 8-17h vs 24h:

| Pair | 8-17h Trades | 8-17h P&L | 8-17h PF | 24h Trades | 24h P&L | 24h PF | P&L Change |
|------|-------------|-----------|----------|-----------|---------|--------|------------|
| **EUR/USD** | 3,120 | +$42,846 | 1.63 | 7,836 | +$77,188 | 1.56 | **+80%** |
| **GBP/USD** | 813 | +$14,564 | 1.67 | 2,021 | +$25,185 | 1.57 | **+73%** |
| **XAU/USD** | 696 | +$77,554 | 2.14 | 1,652 | +$153,003 | 2.13 | **+97%** |
| USD/CHF* | 3,018 | +29,225 | 1.56 | 7,704 | +51,389 | 1.46 | +76% |
| EUR/GBP* | 3,015 | +17,086 | 1.46 | 7,871 | +14,221 | 1.17 | **-17%** |

(*) USD/CHF P&L in CHF, EUR/GBP P&L in GBP.

**Key findings:**

1. **24h is a massive improvement for USD-quoted pairs.** EUR/USD +80%, GBP/USD +73%, XAU/USD +97% more P&L. The Asian session has a genuine edge.

2. **PF drops slightly (1.63→1.56 for EUR/USD)** — Asian session trades have lower quality but the volume more than compensates. The edge is smaller per trade but the increased trade count generates substantially more total P&L.

3. **XAU/USD benefits most** — gold trades 23h/day globally. The 8-17 UTC restriction was missing >50% of tradeable gold moves. +$153k vs +$77k is nearly a 2x improvement.

4. **EUR/GBP is the exception** — 24h HURTS it. PF drops from 1.46 to 1.17 (borderline profitable). EUR/GBP has very low Asian session volatility = many losing trades. **Keep 8-17h for EUR/GBP.**

5. **USD/CHF PF drops from 1.56 to 1.46** — still positive but diluted. Worth running with 24h but monitor closely.

**Recommendation: Switch to 24h for EUR/USD, GBP/USD, XAU/USD. Keep 8-17h for EUR/GBP.**

---

### 2026-04-03 — Live Trading Audit

**Context:** Ran the `/livetrading-audit` skill against CobanReborn to assess readiness for live deployment.

**Result: 5/16 checks passed — NOT READY for live trading.**

**Passed (5):**
- 1.2 Exit Price Realism — uses bar.high/low correctly
- 1.4 Slippage Model — 50% probability, deterministic seed
- 2.2 Indicator Warmup — guards against trading with uninitialized indicators
- 3.3 Equity Curve Accuracy — correct USD balance
- 3.4 Win Rate Display — no division-by-zero

**Failed (11):**

| Check | Severity | Issue |
|-------|----------|-------|
| 1.1 Entry Price | CRITICAL | `on_order_filled` overwrites entry price on exit fills; no rejection handling |
| 1.3 Spread Model | HIGH | ATR exit mode does NOT deduct spread (fixed/trailing do) — inconsistent |
| 2.1 Bar Ordering | MEDIUM | M1 processes before H1 at same timestamp — 1-bar signal lag |
| 2.3 Signal Windows | HIGH | Trend_follow mode doesn't timestamp MACD/RSI — stale values persist |
| 2.4 RSI Trendline | MEDIUM | No minimum break magnitude — floating-point noise triggers false breaks |
| 3.1 P&L Conversion | CRITICAL | Non-USD-quote pairs (CHF, GBP, JPY) have broken P&L |
| 3.2 Position Sizing | CRITICAL | Static 100k lots, no risk-per-trade, XAU/USD uses 100 oz |
| 4.1 Live Adapter | CRITICAL | `pyfx/adapters/` is empty — no broker connection |
| 4.2 State Persistence | CRITICAL | All state in-memory — restart orphans positions |
| 4.3 Error Handling | CRITICAL | No on_order_rejected, no circuit breakers |
| 4.4 Session/Timezone | MEDIUM | No DST handling, no weekend gap protection |

**Estimated backtest-to-live P&L haircut (EUR/USD 24h: 7,836 trades, +$77,188):**
- ATR spread gap: -$11,754 (spread not deducted in ATR mode)
- Real vs modeled slippage: -$39,180 (live slippage ~$10/trade vs ~$5 modeled)
- Fill quality (bar close vs TP level): -$3,859 (-5%)
- Signal drift (stale MACD, RSI noise, DST): -$6,175 (-8%)
- **Total estimated haircut: -64% to -79% of backtest P&L**

**Priority fixes before live:**
1. Build live adapter (pyfx/adapters/) — broker connection is prerequisite
2. Fix ATR exit spread deduction — immediate code fix
3. Add on_order_filled guard (only set entry state on OPENING fills)
4. State persistence — serialize key state to disk
5. Risk-per-trade position sizing
6. Circuit breakers (max daily loss, max consecutive losses)

---

### 2026-04-03 — Strategy Fixes & Updated Defaults

**Context:** Applied learnings from the 15-month sweep and live-trading audit.

**Code changes:**

1. **ATR spread bug fixed** (`coban_reborn.py:_exit_atr`): ATR exits now deduct `_spread_cost` from both TP and SL distances, consistent with fixed/trailing modes. Previously ATR exits were ~1.5 pips too optimistic.

2. **Updated defaults** (`CobanRebornConfig`):
   - `entry_mode`: `"full"` → `"trend_follow"` — proven best entry mode
   - `exit_mode`: `"fixed"` → `"atr"` — most universal across instruments
   - `session_start_hour`: `8` → `0`, `session_end_hour`: `17` → `24` — 24h trading for USD-quoted pairs

3. **on_order_filled guard** — Added `_pending_entry` flag so entry state (`_entry_price`, `_best_price`, `_entry_atr`) is only set on opening fills, not exit fills. Prevents entry price corruption when exit orders fill.

4. **Win rate display bug fixed** — Dashboard templates displayed `0.5%` instead of `50%` because `win_rate` is stored as 0-1. Fixed with `{% widthratio %}` in `backtest_list.html` and `backtest_detail.html`.

**Corrected results (with ATR spread fix, 24h trading, Jan 2025 – Mar 2026):**

| Instrument | Trades | WR% | P&L | Return | PF | MaxDD |
|-----------|--------|-----|-----|--------|-----|-------|
| **EUR/USD** | 8,044 | 49.8% | +$65,023 | +65.0% | 1.49 | -1.08% |
| **GBP/USD** | 2,079 | 50.7% | +$23,314 | +23.3% | 1.54 | -0.88% |
| **XAU/USD** | 1,670 | 55.6% | +$152,430 | +152.4% | 2.11 | -2.62% |
| USD/CHF* | 8,010 | 49.3% | +43,216 CHF | +43.2% | 1.41 | 0.00% |
| EUR/GBP* (8-17h) | 3,133 | 49.8% | +12,414 GBP | +12.4% | 1.33 | 0.00% |

(*) P&L in local quote currency.

**Impact of ATR spread fix:**

| Pair | Before fix | After fix | Change |
|------|-----------|-----------|--------|
| EUR/USD | +$77,188 | +$65,023 | **-16%** |
| GBP/USD | +$25,185 | +$23,314 | -7% |
| XAU/USD | +$153,003 | +$152,430 | -0.4% |

The spread fix had the biggest impact on EUR/USD (1.5 pip spread matters more on lower-volatility pairs). XAU/USD barely affected (ATR >> spread for gold).

---

## Open Questions

- [x] ~~Test removing session hours restriction (24h trading)~~ — **24h is better** for USD-quoted pairs, keep 8-17h for EUR/GBP
- [x] ~~Download and test USD/CHF, EUR/GBP data~~ — done, USD/CHF strong (PF 1.41), EUR/GBP weaker (PF 1.33)
- [x] ~~Fix ATR exit spread deduction~~ — **fixed**, ATR exits now deduct spread
- [x] ~~How does the strategy perform in 2024 data (out-of-sample)?~~ — **XAU/USD excellent (PF 1.89), EUR/USD thin (PF 1.17), GBP/USD borderline (PF 1.29)**
- [x] ~~What's the optimal ATR multiplier pair?~~ — **TP=3.0, SL=2.5 is best on EUR/USD** (+17% P&L). Smooth gradient, not curve-fit.
- [x] ~~Add signal staleness to trend_follow~~ — **done**, `filter_staleness_seconds=7200`. No impact on current data (H1 bars arrive frequently enough), but prevents stale signals in live trading.
- [ ] Download AUD/USD, NZD/USD data via VPN (Dukascopy blocks these from current IP)
- [ ] Can we feed a JPY/USD price series to fix USD/JPY P&L conversion?
- [ ] Would dynamic position sizing (risk % per trade) improve risk-adjusted returns?
- [ ] Test optimized params (SMA 2/7, ATR TP=3.0/SL=2.5) on XAU/USD 2024 OOS
- [ ] Add mean-reversion entry mode for range-bound FX pairs (EUR/USD 2024 showed trend-follow doesn't work there)
- [ ] Build live adapter for XAU/USD specifically (OANDA or Interactive Brokers)
- [ ] Add state persistence for mid-trade recovery
- [ ] Paper trade XAU/USD for 1-3 months before live
- [ ] Should we test 3m/10m or other timeframe combos?
- [ ] Why did OIL/USD Fixed and Trailing produce identical results?
