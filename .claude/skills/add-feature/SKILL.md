---
name: add-feature
description: Plan and implement a new feature for pyfx. Use when the user wants to add functionality.
disable-model-invocation: true
---

Plan and implement: $ARGUMENTS

## Implementation checklist

1. **Explore** the relevant existing code before writing anything. Understand the patterns in:
   - CLI: `pyfx/cli.py` — Click commands, options, lazy imports
   - Config: `pyfx/core/config.py` — Pydantic settings with `PYFX_` prefix
   - Types: `pyfx/core/types.py` — dataclasses for BacktestConfig, TradeRecord, etc.
   - Strategies: `pyfx/strategies/base.py` — PyfxStrategy base class (wraps NautilusTrader Strategy)
   - Strategy discovery: `pyfx/strategies/loader.py` — entry points + directory scanning
   - Backtest engine: `pyfx/backtest/runner.py` — NautilusTrader BacktestEngine integration
   - Django dashboard: `pyfx/web/dashboard/` — models, views, URLs

2. **Plan** the changes. Identify all files that need modification. For new models, plan migrations. For new CLI commands, plan Click decorators.

3. **Implement** following these rules:
   - New strategies: extend `PyfxStrategy`, implement `on_start()` and `on_bar()`, register via entry points in `pyproject.toml`
   - New CLI commands: add to `pyfx/cli.py` under `@main.command()`, use Click options with help text
   - New config: add to `PyfxSettings` in `pyfx/core/config.py` with `PYFX_` prefix
   - New types: add dataclasses to `pyfx/core/types.py`
   - New Django models: add to `pyfx/web/dashboard/models.py`, create migration
   - Full type hints required (mypy strict mode)
   - Use lazy imports for heavy packages (NautilusTrader, pandas, Django) inside CLI commands

4. **Verify** the feature works:
   ```bash
   pyfx backtest --strategy sample_sma --start 2023-01-01 --end 2023-03-01 --data-file ~/.pyfx/data/EURUSD_365d_M1.parquet
   ```
   Or run the relevant CLI command for the new feature.

5. **Test**: add tests to `tests/`, run `pytest --cov=pyfx --cov-fail-under=100 tests/`, ensure 100% coverage maintained.

## Architectural constraints

- Python 3.12+ with full type hints (mypy strict)
- NautilusTrader for backtesting engine — don't reinvent simulation logic
- Pydantic for config and data validation
- Click for CLI
- Django + DRF for web dashboard (optional extra)
- No npm, no frontend build tools
