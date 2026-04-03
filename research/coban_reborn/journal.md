# CobanReborn Strategy Research Journal

**Status:** Active — trend_follow mode validated across 5 instruments
**Verdict:** Trend Follow entry + ATR exits on 5m/15m is the best general-purpose config. Original "full" confluence mode produces 0 trades on real data.

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

```bash
# EUR/USD (or GBP/USD) — best for FX pairs
uv run pyfx backtest -s coban_reborn \
  --data-file ~/.pyfx/data/EURUSD_2025-2026_M1.parquet \
  --start 2025-01-01 --end 2025-06-30 \
  --extra-bar-type 5-MINUTE-LAST-EXTERNAL \
  --extra-bar-type 15-MINUTE-LAST-EXTERNAL \
  -p entry_mode=trend_follow -p exit_mode=atr \
  --save

# XAU/USD — use smaller trade_size (100 oz vs 100k FX units)
uv run pyfx backtest -s coban_reborn -i XAU/USD \
  --data-file ~/.pyfx/data/XAUUSD_2025-2026_M1.parquet \
  --start 2025-01-01 --end 2025-03-31 \
  --extra-bar-type 5-MINUTE-LAST-EXTERNAL \
  --extra-bar-type 15-MINUTE-LAST-EXTERNAL \
  -p entry_mode=trend_follow -p exit_mode=atr -p trade_size=100 -p spread_pips=3 \
  --save
```

**Key parameters (defaults):**
- `entry_mode=trend_follow` — SMA cross trigger + MACD/RSI filters
- `exit_mode=atr` — ATR(14) based TP/SL, multipliers 2.0/1.5
- `sma_fast_period=4`, `sma_slow_period=9`
- `rsi_level_threshold=0.50` — RSI above 0.5 for longs, below for shorts
- `session_start_hour=8`, `session_end_hour=17` (London/NY session)
- `spread_pips=1.5` (FX), `3.0` (commodities)

---

## Key Findings

1. **The original 5-layer confluence is too strict.** All three H1 signals (SMA cross + MACD zero-cross + RSI trendline break) must coincide within 1 hour. This essentially never happens on real data. 0 trades on 6 months of EURUSD.

2. **Trend Follow is the best entry mode.** Using SMA cross as the *trigger* (not requiring coincidence with other crossovers) and MACD/RSI as *directional filters* (histogram positive? RSI above 0.5?) produces 10-15x more trades while maintaining edge.

3. **5m/15m timeframe dramatically outperforms 1h/2h.** Every single variation tested produced more total P&L on 5m/15m. More signals fire, more trades, higher total return. Win rate drops slightly (90% -> 50%) but profit factor stays strong (1.7-2.2).

4. **ATR exits are the most universal.** Fixed pip TP/SL only works for FX pairs where pip values are similar. Gold at $3000 needs different pip scaling than EUR/USD at $1.10. ATR auto-adapts to any instrument's volatility.

5. **Non-FX instruments need custom setup.** `TestInstrumentProvider.default_fx_ccy()` gives wrong precision for gold/oil. Fixed in runner.py with `_INSTRUMENT_OVERRIDES` dict. Also need adjusted `trade_size` (100 oz for gold, 1000 bbl for oil vs 100k for FX).

6. **USD/JPY P&L is unreliable.** NautilusTrader can't convert JPY-denominated P&L to USD without a separate price feed. Win rate and profit factor are valid; absolute dollar P&L is in JPY terms.

7. **Backtest realism matters.** Adding 50% slippage probability and intra-bar high/low exit checks reduced P&L by ~10% vs. naive bar-close exits. Still profitable across all instruments.

---

## Research Log

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

## Open Questions

- [ ] Can we feed a JPY/USD price series to fix USD/JPY P&L conversion?
- [ ] Would dynamic position sizing (risk % per trade) improve risk-adjusted returns?
- [ ] How does the strategy perform in 2024 data (out-of-sample)?
- [ ] Should we test 3m/10m or other timeframe combos?
- [ ] What's the optimal ATR multiplier pair? (currently 2.0 TP / 1.5 SL)
