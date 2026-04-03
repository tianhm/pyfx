# CLAUDE.md - pyfx

## Project Overview

pyfx is a backtesting and live trading tool for forex and other instruments, powered by NautilusTrader. Python 3.12+, Click CLI, optional Django dashboard for browsing results.

## Architecture

```
CLI (pyfx/cli.py)
  -> backtest/runner.py -> NautilusTrader BacktestEngine
                        -> PyfxStrategy (pyfx/strategies/)
Results -> Django DB (pyfx/web/dashboard/) -> Web views + JSON APIs
```

**Key flow:** User provides bar data (CSV/Parquet) + strategy name. The backtest runner creates a NautilusTrader `BacktestEngine`, loads the strategy, feeds it bars, and extracts results (trades, equity curve, metrics).

## Project Structure

```
pyfx/
  __init__.py
  cli.py                     # Click CLI entry point (backtest, strategies, generate-sample-data, web)
  core/
    config.py                # Pydantic settings (PYFX_ prefix, .env support)
    types.py                 # Pydantic models: BacktestConfig, BacktestResult, TradeRecord, EquityPoint
  strategies/
    base.py                  # PyfxStrategy base class (wraps NautilusTrader Strategy)
    loader.py                # Strategy discovery via entry points + directory scanning
    sample_sma.py            # SMA crossover demo strategy
  backtest/
    runner.py                # NautilusTrader BacktestEngine integration
  adapters/                  # (future) live broker adapters
  web/
    pyfx_web/settings.py     # Django settings
    dashboard/               # Django app: models, views, URLs, migrations
tests/
  test_loader.py             # Strategy discovery tests
  test_sample_strategy.py    # SMA backtest smoke test
```

## Commands

```bash
pyfx backtest -s <strategy> --start <date> --end <date> --data-file <path>  # Run a backtest
pyfx strategies                                                              # List available strategies
pyfx generate-sample-data                                                    # Create synthetic test data
pyfx web                                                                     # Start Django dashboard
```

## Configuration

Pydantic settings with `PYFX_` prefix. Supports `.env` files.

| Variable | Description | Default |
|----------|-------------|---------|
| `PYFX_DATA_DIR` | Local data cache | `~/.pyfx/data` |
| `PYFX_CATALOG_DIR` | NautilusTrader Parquet catalog | `~/.pyfx/catalog` |
| `PYFX_STRATEGIES_DIR` | Extra directory to scan for strategy modules | None |
| `PYFX_DEFAULT_BALANCE` | Starting balance (USD) | 100,000 |
| `PYFX_DEFAULT_LEVERAGE` | Leverage ratio | 50 |
| `PYFX_DB_PATH` | SQLite database path | `~/.pyfx/db.sqlite3` |
| `PYFX_SECRET_KEY` | Django secret key | dev default (change in prod) |

## Quality Gates (mandatory before commit)

1. `ruff check pyfx/` — zero errors
2. `mypy pyfx/` — zero errors (strict mode)
3. `pytest --cov=pyfx --cov-report=term-missing --cov-fail-under=100 tests/` — all pass, 100% coverage
4. Run `/checking-quality` to do all of the above + security review

## Available Skills

- `/checking-quality` — lint + type check + security review + tests + 100% coverage
- `/to-master` — finalize worktree -> commit -> merge to master
- `/perf-sweep` — performance audit (engine, data I/O, vectorization, memory, dashboard)
- `/add-feature` — plan and implement a new feature
- `/fix-issue` — debug and fix a reported bug
- `/ux-audit` — CLI ergonomics, output readability, dashboard usability audit

## Strategy Development

1. Create a new file in `pyfx/strategies/`
2. Extend `PyfxStrategy` (from `pyfx/strategies/base.py`)
3. Implement `on_start()` and `on_bar(bar)` — use convenience methods: `market_buy()`, `market_sell()`, `close_all()`, `flat()`, `is_long()`, `is_short()`
4. Register via entry points in `pyproject.toml`:
   ```toml
   [project.entry-points."pyfx.strategies"]
   my_strategy = "pyfx.strategies.my_strategy:MyStrategy"
   ```
5. Or place the file in `PYFX_STRATEGIES_DIR` for auto-discovery

## Development

```bash
pip install -e ".[all]"                    # Install with all extras (web + dev)
ruff check pyfx/                           # Lint
mypy pyfx/                                 # Type check
pytest --cov=pyfx tests/                   # Tests with coverage
```

### Coverage target: 100%

All new code must include tests. No exceptions.

## Coding Conventions

- Full type hints required (mypy strict mode)
- Pydantic models for config and data validation (`pyfx/core/types.py`, `pyfx/core/config.py`)
- Business logic in `backtest/`, `strategies/`, `core/` — not in `cli.py`
- Lazy imports for heavy packages (NautilusTrader, pandas, Django) inside Click commands
- Environment-based config via `PYFX_` prefix (no hardcoded secrets)
- Decimal types for trade sizes and money precision
- Click decorators for all CLI commands with `help=` on every option

## Gotchas

- **NautilusTrader bar types**: must match format `step-aggregation-price_type-source` (e.g., `1-MINUTE-LAST-EXTERNAL`)
- **Data files**: must have OHLCV columns (`open`, `high`, `low`, `close`, `volume`) with a DatetimeIndex
- **Timezone handling**: bar data index must be UTC. `_load_data()` auto-localizes naive timestamps
- **Django dashboard**: uses SQLite at `~/.pyfx/db.sqlite3`, auto-migrates on `pyfx web` startup
- **Strategy discovery**: checks BOTH entry points AND `PYFX_STRATEGIES_DIR` — strategies from either source are available
- **`.travis.yml` is outdated** — targets Python 2.7 from the old codebase. Ignore it.
- **`README.rst` is outdated** — still references OANDA, TA-Lib, old architecture. CLAUDE.md is the dev reference.
- **Worktree merge**: must `cd /Users/joseph/Coding/private/pyfx-cli` to merge since `master` is checked out there

## Security

- No hardcoded API tokens or secrets — use `PYFX_*` env vars
- Django secret key must be set via `PYFX_SECRET_KEY` in production
- Validate user-provided file paths (no path traversal via `--data-file`)
- Timeouts on all HTTP requests
- No bare `except:` — always catch specific exceptions
- No `eval`/`exec`/`pickle` on untrusted data
- Dashboard views should use `login_required` when auth is added
