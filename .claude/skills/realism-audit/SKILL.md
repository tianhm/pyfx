---
name: realism-audit
description: Audit backtest realism for pyfx strategies. Checks that backtests model real-world trading conditions (slippage, spreads, intra-bar fills, position sizing) to avoid overfitting.
user_invocable: true
---

Audit the backtest engine and strategy code for realism gaps that would make results unreliable for live trading. Report each gap with severity and a fix recommendation.

## Checklist

### 1. Fill Model (Critical)

Check `pyfx/backtest/runner.py` for:
- **Slippage**: A `FillModel` with `prob_slippage > 0` must be passed to `engine.add_venue()`. Without it, market orders fill at exact bar prices (unrealistic).
- **Current standard**: `FillModel(prob_fill_on_limit=1.0, prob_slippage=0.5, random_seed=42)` — 50% chance of 1-tick slip.
- If missing or `prob_slippage=0.0`, flag as CRITICAL.

### 2. TP/SL Intra-bar Checks (Critical)

Check strategy exit logic for how TP/SL levels are evaluated:
- **Must use**: bar `high` for long TP / short SL; bar `low` for long SL / short TP.
- **Must NOT use**: bar `close` for TP/SL — this is look-ahead bias (close is unknown intra-bar).
- Pattern to look for: `float(bar.close)` in exit logic = BUG. Should be `float(bar.high)` / `float(bar.low)`.

### 3. Spread Handling (High)

- Spread should reduce TP distance and increase SL distance (cost of entry).
- Check `spread_pips` config is non-zero (default 1.5 for FX).
- For non-FX instruments (gold, oil), spread needs scaling to match the instrument's pip size.

### 4. Commission Model (Medium)

- `MakerTakerFeeModel()` uses instrument's default fees (0.002% for FX via TestInstrumentProvider).
- Real retail FX cost is dominated by spread, not commission — so the low fee is acceptable IF spread is modeled.
- Flag if both spread AND fees are zero.

### 5. Position Sizing (Medium)

- Check if trade size is static (same size every trade) vs. dynamic (risk-adjusted).
- Static `100000` lots is acceptable for strategy comparison but unrealistic for equity curve accuracy.
- Flag if position size exceeds reasonable leverage for account balance.

### 6. Instrument Configuration (Medium for non-FX)

- `TestInstrumentProvider.default_fx_ccy()` gives 5-decimal precision for ALL pairs including XAU/USD and OIL/USD.
- For gold (~$3000), pip = 0.0001 means a 10-pip move = $0.001 — far too small.
- **Fix**: Use ATR-based exits for non-FX instruments (auto-adapts to volatility scale).
- Fixed pip TP/SL for gold need ~300x scaling vs. EUR/USD.

### 7. Session Filtering (Low)

- Trading hours filter should match the instrument's liquid hours.
- Default 08-17 UTC is reasonable for London/NY FX session.
- For commodities (oil, gold), may need different hours.

### 8. Look-Ahead Bias (Critical if found)

- Signal generation must only use data available at bar time.
- Check that indicators are fed bar-by-bar, not batch-calculated.
- Check timestamp comparisons use `ts_init` (when bar was received), not future timestamps.

## Output Format

```
REALISM AUDIT — <strategy name>
==========================================
[PASS/FAIL] Fill Model: <details>
[PASS/FAIL] Intra-bar Exits: <details>
[PASS/FAIL] Spread Model: <details>
[PASS/FAIL] Commission Model: <details>
[PASS/FAIL] Position Sizing: <details>
[PASS/FAIL] Instrument Config: <details>
[PASS/FAIL] Session Filter: <details>
[PASS/FAIL] Look-Ahead Bias: <details>

Overall: X/8 checks passed
Estimated P&L overfit: <percentage range if gaps found>
```

## Notes

- This audit complements `/checking-quality` — run both before trusting backtest results.
- ATR-based exits (`exit_mode="atr"`) are the safest for multi-instrument strategies.
- The `random_seed=42` in FillModel ensures deterministic tests but same slippage pattern — consider varying seed for robustness testing.
