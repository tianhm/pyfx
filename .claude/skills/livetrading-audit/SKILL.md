---
name: livetrading-audit
description: Audit pyfx strategies for backtest-to-live fidelity. Checks that a strategy would produce identical signals, fills, and P&L in live trading as it does in backtest — catches the bugs that make backtests profitable but live trading lose money.
user_invocable: true
---

Audit the strategy and backtest engine to find every place where live trading behavior would diverge from backtest results. The goal is simple: if the backtest says +20%, live should be +20% (minus realistic execution costs). Any gap between backtest and live is a bug.

Run all checks against the strategy specified in `$ARGUMENTS` (default: scan all strategies in `pyfx/strategies/`).

## Phase 1 — Execution Fidelity (will you get the same fills?)

### 1.1 Entry Price Assumptions (Critical)

Read the strategy's `on_order_filled()` and entry logic in `_on_m1_bar()`:
- **Check**: Does `on_order_filled` guard against close-fill overwrites? It must only set `_entry_price`, `_best_price`, `_entry_atr` when OPENING a new position (`not self.flat()` and `_trade_direction != 0`). If it fires on exit fills too, entry state is corrupted silently.
- **Check**: Is there a gap between setting `_trade_direction` and calling `market_buy()`/`market_sell()`? If the order fills asynchronously (live), another bar could arrive between direction-set and fill.
- **Check**: Does the strategy handle the case where `market_buy()` is called but the order is rejected or partially filled? In backtest, orders always fill. In live, they can fail.

### 1.2 Exit Price Realism (Critical)

Read the strategy's `_exit_fixed()`, `_exit_trailing()`, `_exit_atr()`:
- **Check**: Exit uses `bar.high` / `bar.low` (not `bar.close`) for TP/SL evaluation. Using close is look-ahead bias.
- **Check**: When TP is hit, `close_all()` is called with a market order that fills at bar close. But TP was hit at the high/low — the actual fill should be at the TP price, not bar close. This means the backtest over- or under-counts P&L by the distance between TP level and bar close. Flag the magnitude.
- **Check**: Spread is deducted from TP distance (correct — you need to move further to profit) and ADDED to SL distance (correct — your loss is bigger with spread). If spread is subtracted from SL, that's a bug (tighter stop = more losses).
- **Check**: ATR exits and trailing exits also account for spread. If they don't, they're optimistic vs. fixed exits.

### 1.3 Spread Model (High)

- **Check**: `spread_pips` is non-zero in config defaults.
- **Check**: Spread is constant in backtest but varies 2-10x in live during news, session open/close, and low liquidity.
- **Estimate impact**: For a strategy with N trades and average hold time T, calculate `N * spread_pips * pip_value` as the minimum execution cost. Compare to total P&L. If spread cost > 30% of P&L, the edge may not survive live spreads.
- **Check for non-FX**: Gold spread is typically 30-50 cents ($0.30-0.50), oil is $0.03-0.05. Are `spread_pips` values realistic for each instrument?

### 1.4 Slippage Model (High)

Read `runner.py` FillModel configuration:
- **Check**: `prob_slippage > 0` (should be 0.5 = 50% chance of 1-tick slip).
- **Check**: Slippage is 1-tick only. In live, slippage can be 2-5 ticks during fast markets, news, or illiquid hours.
- **Check**: `random_seed=42` makes slippage deterministic. Run the same strategy with seeds 1, 42, 100 — if P&L variance > 10%, the strategy is slippage-sensitive and unreliable live.
- **Estimate impact**: Calculate `num_trades * 2 * avg_slippage_pips * pip_value` (entry + exit slip). Compare to total P&L.

## Phase 2 — Signal Fidelity (will you get the same signals?)

### 2.1 Multi-Timeframe Bar Ordering (Critical)

Read `runner.py` bar loading and `strategy.on_bar()`:
- **Check**: Backtest uses `engine.sort_data()` to globally sort M1, H1, H2 bars by timestamp. When an H1 bar closes at 10:00, it arrives in the event stream BEFORE the M1 bar at 10:00. In live, H1 bar may arrive AFTER several M1 bars due to feed latency.
- **Check**: Does the strategy depend on H1 signals being available before M1 entry? If `_on_h1_bar()` sets signals and `_on_m1_bar()` uses them in the same second, the order matters. In live, this ordering is non-deterministic.
- **Recommendation**: Strategy should tolerate H1 arriving 1-2 M1 bars late. Test by delaying H1 bar processing and verifying signal timing still works.

### 2.2 Indicator Warmup & Cold Start (High)

Read indicator initialization in `__init__()` and `on_start()`:
- **Check**: How many bars until all indicators are initialized? SMA(9) needs 9 bars, RSI(14) needs 14, ATR(14) needs 14, MACD EMA(26) needs 26. On H1 bars, that's 26 hours of data.
- **Check**: What happens if the strategy is deployed mid-session? The RSI buffer (`_h1_rsi_buf`) grows incrementally — first 100 H1 bars (~4 days) will have incomplete trendline detection. Are there guards against trading with incomplete indicators?
- **Check**: MACD signal-line EMA is seeded with first value (`_h1_macd_count == 1`). On strategy restart, signal line diverges from a fully warmed MACD for ~9 bars. This creates false histogram crossovers.
- **Check**: `_h1_prev_sma_diff = 0.0` on startup means the first SMA crossover is missed. In backtest this costs 1 signal. In live with frequent restarts, this loses signals repeatedly.

### 2.3 Signal Timestamp Windows (Medium)

Read `_signals_within_window()`, `_is_fresh()`, and signal timestamp tracking:
- **Check**: `max_signal_window_seconds` (default 3600 = 1 hour) determines how long signals stay valid. If a connection drop causes M1 bars to arrive late, stale signals may falsely expire.
- **Check**: Double-confirm logic (`double_confirm_window_seconds`, `double_confirm_min_gap_seconds`) requires precise timing between signal occurrences. Network jitter of even 1 second can push a signal outside the window.
- **Check**: Are timestamps from `bar.ts_init` (bar creation time) or `bar.ts_event` (bar close time)? Backtest uses `ts_init`. Live feeds may have different semantics.

### 2.4 RSI Trendline Break Sensitivity (Medium)

Read `detect_rsi_trendline_break()` and related functions:
- **Check**: Local extrema detection uses strict inequality (`values[i] > values[i-1] and values[i] > values[i+1]`). A 1-tick difference in RSI creates/destroys an extremum, changing the trendline entirely. In live, RSI values may differ by floating-point rounding vs. backtest.
- **Check**: `rsi_min_peak_diff=2` means extrema only 2 bars apart are valid. This is very sensitive to noise. Flag if this parameter is below 3.
- **Check**: Trendline slope validation examines ALL peaks in the buffer. As new peaks arrive, old trendlines become invalid. This means the same RSI sequence can produce different break signals depending on buffer history — fragile for live.

## Phase 3 — P&L Fidelity (will you make the same money?)

### 3.1 P&L Currency Conversion (Critical)

Read `_extract_results()` in `runner.py`:
- **Check**: For non-USD-quote pairs (USD/JPY, USD/CHF, USD/CAD), NautilusTrader reports `realized_pnl` in the quote currency (JPY, CHF, CAD). The results extractor must convert to USD using `_convert_pnl_to_usd()`. If it doesn't, P&L and return % are wildly wrong (e.g., 914% instead of 5.9% for USD/JPY).
- **Check**: The conversion uses `avg_px_close` for the exchange rate. This is approximate — live P&L uses the actual fill price. The error is small but non-zero.

### 3.2 Position Sizing vs. Account Balance (Critical)

- **Check**: `trade_size` in config vs. actual account balance and leverage. If `trade_size=100000` (1 standard lot) with $3,000 balance at 50x leverage, that's $100k / $150k max = 66% of margin on one trade. A 30-pip SL = $300 = 10% account risk per trade.
- **Check**: Is there a risk-per-trade limit? Proper sizing: `position_size = (equity * risk_pct) / sl_distance`. Without this, backtest P&L is not scalable to live.
- **Check**: Backtest starts with `config.balance` (default 100k). If live account is $3k, scaling trade_size down by 33x also scales P&L down by 33x. A $21k backtest profit becomes $636. Is that still net positive after realistic costs?

### 3.3 Equity Curve Accuracy (Medium)

Read equity curve extraction from `account_report`:
- **Check**: Account balance in `generate_account_report()` should be in the account's base currency (USD). For non-USD-quote pairs, verify the balance column reflects USD, not quote currency.
- **Check**: Drawdown calculation uses `cummax()` on balance. If balance is in wrong currency, max drawdown % is wrong.

### 3.4 Win Rate & Profit Factor Sanity (Medium)

- **Check**: `win_rate` is stored as a fraction (0.0-1.0). If displayed with a `%` sign, it must be multiplied by 100 first. Raw `0.50` displayed as "0.5%" is a bug.
- **Check**: `profit_factor = gross_wins / gross_losses`. If either is zero, handle correctly (None, not division by zero).
- **Check**: `avg_win` and `avg_loss` must be in the same currency. If some trades are in JPY and others in USD, averages are meaningless.

## Phase 4 — Deployment Readiness

### 4.1 Live Adapter Exists (Critical)

- **Check**: Does `pyfx/adapters/` contain a live execution adapter? If empty, the strategy CANNOT be deployed live regardless of other checks.
- **Check**: If an adapter exists, does it implement: order submission, fill callbacks, position reconciliation, connection recovery, and heartbeat monitoring?

### 4.2 State Persistence (High)

- **Check**: If the strategy or the connection restarts, can it recover? What state is lost?
  - `_entry_price`, `_trade_direction` — lost, meaning open positions have no SL/TP tracking
  - `_h1_rsi_buf` — lost, trendline detection broken for 100 H1 bars (~4 days)
  - `_h1_macd_signal`, `_h1_macd_count` — lost, MACD histogram jumps on restart
  - All signal timestamps — lost, no pending signals survive restart
- **Check**: Is there a mechanism to serialize/deserialize strategy state? If not, flag as HIGH — any restart during a trade means unmanaged risk.

### 4.3 Error Handling (High)

- **Check**: Does `market_buy()`/`market_sell()` handle exceptions? In live, order submission can raise on connection loss, invalid quantity, insufficient margin.
- **Check**: Does `close_all()` retry on failure? If the close order fails, the strategy thinks it's flat but has an open position.
- **Check**: Are there circuit breakers? Max daily loss, max consecutive losses, max position count?

### 4.4 Session & Timezone Handling (Medium)

- **Check**: Session filter uses `_bar_hour_utc()` which extracts UTC hour from nanosecond timestamp. No DST handling.
- **Check**: FX market closes at 5pm EST Friday, reopens 5pm EST Sunday. Does the strategy avoid trading during the weekend gap?
- **Check**: For instruments with specific trading hours (futures, stocks), does the session filter match?

## Output Format

```
LIVE TRADING AUDIT — <strategy name>
============================================================

PHASE 1: EXECUTION FIDELITY
[PASS/FAIL] 1.1 Entry Price Assumptions: <details>
[PASS/FAIL] 1.2 Exit Price Realism: <details>
[PASS/FAIL] 1.3 Spread Model: <details>
[PASS/FAIL] 1.4 Slippage Model: <details>

PHASE 2: SIGNAL FIDELITY
[PASS/FAIL] 2.1 Multi-TF Bar Ordering: <details>
[PASS/FAIL] 2.2 Indicator Warmup: <details>
[PASS/FAIL] 2.3 Signal Timestamp Windows: <details>
[PASS/FAIL] 2.4 RSI Trendline Sensitivity: <details>

PHASE 3: P&L FIDELITY
[PASS/FAIL] 3.1 P&L Currency Conversion: <details>
[PASS/FAIL] 3.2 Position Sizing: <details>
[PASS/FAIL] 3.3 Equity Curve Accuracy: <details>
[PASS/FAIL] 3.4 Win Rate Display: <details>

PHASE 4: DEPLOYMENT READINESS
[PASS/FAIL] 4.1 Live Adapter: <details>
[PASS/FAIL] 4.2 State Persistence: <details>
[PASS/FAIL] 4.3 Error Handling: <details>
[PASS/FAIL] 4.4 Session/Timezone: <details>

============================================================
Overall: X/16 checks passed
Live-readiness: [NOT READY / READY WITH CAVEATS / READY]

Estimated backtest-to-live P&L haircut:
  Spread costs:   -$X (N trades * spread)
  Slippage costs: -$X (N trades * avg slip)
  Fill quality:   -X% (TP/SL fill gap)
  Signal drift:   -X% (bar ordering, warmup)
  TOTAL:          -X% to -X% of backtest P&L
```

## Process

1. Read the strategy file(s) end-to-end. Do NOT skim — read every line of signal detection, entry, exit, and state management.
2. Read `runner.py` fill model, instrument setup, and results extraction.
3. Read `base.py` order submission and position tracking.
4. For each check, cite the specific file:line where the issue exists or is handled correctly.
5. Calculate dollar estimates for execution costs where possible.
6. After reporting, fix any CRITICAL or HIGH issues directly in code.
7. Run `/checking-quality` after fixes.

## Notes

- This audit is stricter than `/realism-audit`. Realism-audit checks if the backtest is reasonable. This audit checks if it would reproduce identically in live.
- A strategy can pass realism-audit but fail livetrading-audit — realism says "close enough", livetrading says "exactly the same".
- The most common cause of backtest-to-live divergence is NOT bugs — it's the gap between bar-close execution (backtest) and market-order execution (live). A 1-pip difference per trade across 500 trades = $5,000 on a standard lot.
- Run this audit every time strategy logic changes, not just before deployment.
