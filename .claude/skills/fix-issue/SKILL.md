---
name: fix-issue
description: Analyze and fix a bug or issue in the pyfx codebase. Use when the user reports a problem.
disable-model-invocation: true
---

Analyze and fix the issue described: $ARGUMENTS

## Workflow

1. **Understand the problem.** Read the relevant source files in `pyfx/`. Identify which layer is affected:
   - CLI (`pyfx/cli.py`) — argument parsing, output formatting
   - Config (`pyfx/core/config.py`) — environment variables, defaults
   - Backtest engine (`pyfx/backtest/runner.py`) — NautilusTrader integration, data wrangling
   - Strategies (`pyfx/strategies/`) — strategy logic, indicator calculations
   - Strategy loader (`pyfx/strategies/loader.py`) — discovery, import issues
   - Django dashboard (`pyfx/web/`) — models, views, templates

2. **Reproduce if possible.** Run a backtest that triggers the issue:
   ```bash
   pyfx backtest --strategy <name> --start <date> --end <date> --data-file <path> --log-level DEBUG
   ```
   Check CLI output for errors, NautilusTrader logs for warnings.

3. **Implement the fix.** Follow existing patterns:
   - Pydantic for data validation at boundaries
   - NautilusTrader types for market data (don't roll custom types)
   - Decimal for money/price precision
   - Lazy imports for heavy packages inside CLI commands
   - Full type hints (mypy strict)

4. **Verify the fix.** Re-run the backtest, check results match expectations.

5. **Test and commit.** Add/update tests covering the fix. Run `/checking-quality`. Create a commit with a descriptive message explaining the root cause and fix.
