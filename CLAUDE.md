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
    coban_reborn.py          # Multi-timeframe strategy: "full" confluence or "trend_follow" mode
    coban_experimental.py    # Experimental testbed for strategy variations (7 entry × 3 exit modes)
  data/
    dukascopy.py             # Dukascopy CSV ingestion → OHLCV Parquet
  backtest/
    runner.py                # NautilusTrader BacktestEngine integration
  adapters/                  # (future) live broker adapters
  web/
    pyfx_web/settings.py     # Django settings
    dashboard/               # Django app: models, views, URLs, migrations
      management/commands/
        run_backtest.py      # CLI management command to run + save backtest
        run_backtest_web.py  # Subprocess command for web-triggered backtests
research/
  README.md                  # Research journal format and conventions
  coban_reborn/journal.md    # CobanReborn strategy research journal
scripts/
  coban_sweep.py             # 10-variation backtest sweep (EUR/USD)
  coban_multi_pair.py        # Multi-pair sweep (5 instruments)
  param_sensitivity.py       # Parameter sensitivity sweep (perturb +/- 40%)
  walk_forward.py            # Walk-forward analysis (rolling 3-month windows)
tests/
  conftest.py                # pytest-django configuration
  test_loader.py             # Strategy discovery tests
  test_sample_strategy.py    # SMA backtest smoke test
  test_coban_reborn.py       # CobanReborn strategy + multi-TF infrastructure tests
  test_ingest.py             # Dukascopy CSV ingestion tests
  test_web.py                # Django dashboard views, APIs, management commands
```

## Commands

```bash
uv run pyfx backtest -s <strategy> --start <date> --end <date> --data-file <path>  # Run a backtest
uv run pyfx backtest -s coban_reborn ... --extra-bar-type 5-MINUTE-LAST-EXTERNAL --extra-bar-type 15-MINUTE-LAST-EXTERNAL  # Default: trend_follow + ATR + 24h
uv run pyfx backtest -s coban_reborn ... --extra-bar-type 5-MINUTE-LAST-EXTERNAL --extra-bar-type 15-MINUTE-LAST-EXTERNAL -p session_start_hour=8 -p session_end_hour=17  # London/NY hours only (EUR/GBP)
uv run pyfx backtest ... --seed 0                                                                                          # Random slippage seed (default: 42)
uv run pyfx strategies                                                              # List available strategies
uv run pyfx generate-sample-data                                                    # Create synthetic test data
uv run pyfx ingest -i <csv> [-o <parquet>]                                          # Ingest Dukascopy CSV to Parquet
uv run pyfx web                                                                     # Start Django dashboard
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

1. `uv run ruff check pyfx/` — zero errors
2. `uv run mypy pyfx/` — zero errors (strict mode)
3. `uv run pytest --cov=pyfx --cov-report=term-missing --cov-fail-under=100 tests/` — all pass, 100% coverage
4. Run `/checking-quality` to do all of the above + security review

## Available Skills

- `/checking-quality` — lint + type check + security review + tests + 100% coverage
- `/to-master` — finalize worktree -> commit -> merge to master
- `/perf-sweep` — performance audit (engine, data I/O, vectorization, memory, dashboard)
- `/add-feature` — plan and implement a new feature
- `/fix-issue` — debug and fix a reported bug
- `/ux-audit` — CLI ergonomics, output readability, dashboard usability audit
- `/realism-audit` — audit backtest realism (slippage, intra-bar fills, spreads, position sizing)

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
uv sync --all-extras                       # Install with all extras (web + dev + data)
uv run ruff check pyfx/                    # Lint
uv run mypy pyfx/                          # Type check
uv run pytest --cov=pyfx tests/            # Tests with coverage
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
- **Django dashboard**: uses SQLite at `~/.pyfx/db.sqlite3`, auto-migrates on `pyfx web` startup. Sidebar layout with DaisyUI drawer. Overview at `/`, backtests at `/backtests/`. Web-triggered backtests run via `run_backtest_web` management command in a subprocess.
- **Django setup in CLI**: use `_setup_django()` from `pyfx/cli.py` instead of inline `os.environ.setdefault(...); django.setup()`. The helper is idempotent.
- **mypy + Django stubs**: `django-stubs` is configured via `mypy_django_plugin.main` in `pyproject.toml`. NautilusTrader has no stubs — its imports use `ignore_missing_imports` in mypy overrides. Migrations are excluded from mypy checking.
- **Strategy config classes**: extend NautilusTrader's `StrategyConfig` (msgspec.Struct), NOT Pydantic. Use `__struct_fields__` and `msgspec.structs.fields()` for introspection, not `model_fields`.
- **pytest-django**: required for web tests. Configure via `DJANGO_SETTINGS_MODULE` in `pyproject.toml` `[tool.pytest.ini_options]`.
- **Strategy discovery**: checks BOTH entry points AND `PYFX_STRATEGIES_DIR` — strategies from either source are available
- **NautilusTrader RSI range**: `RelativeStrengthIndex.value` returns 0.0–1.0 (not 0–100). Strategy thresholds must use 0.30/0.70 not 30/70
- **Dukascopy data download**: `duka` Python package is broken; use `npx dukascopy-node` instead. Dukascopy may block certain IPs — use VPN if timeouts occur
- **Package manager**: use `uv` (not `pip`) for all package management
- **Multi-timeframe backtests**: use `--extra-bar-type` (repeatable) on the CLI. Runner resamples M1 data to higher timeframes. Strategy receives bars via `on_bar()` — dispatch by `bar.bar_type`. `PyfxStrategyConfig.extra_bar_types` must be a `tuple` (frozen struct).
- **MACD histogram**: NautilusTrader's `MovingAverageConvergenceDivergence` only provides the MACD line (`.value`), not the signal line or histogram. Compute these manually using two EMAs + a signal-line EMA.
- **NautilusTrader indicators import**: Use `from nautilus_trader.indicators import RelativeStrengthIndex, SimpleMovingAverage, ExponentialMovingAverage` (top-level `indicators` module, not submodules like `indicators.rsi`)
- **Worktree merge**: must `cd /Users/joseph/Coding/private/pyfx-cli` to merge since `master` is checked out there
- **Backtest realism**: Runner uses `FillModel(prob_slippage=0.9)` for 90% chance of 1-tick slippage on fills. Seed configurable via `--seed` (default 42, `--seed 0` = random). Exit TP/SL checks use bar high/low (not close) for realistic intra-bar fills. `MakerTakerFeeModel` fees are low (0.002%) — spreads are the real cost for FX.
- **Signal staleness**: Trend_follow mode timestamps MACD histogram and RSI values via `filter_staleness_seconds` (default 7200s = 2 H1 bars). Stale filter values are rejected to prevent trading on outdated signals.
- **Next-bar entry**: `next_bar_entry=True` defers entry to the next M1 bar open (more realistic timing). Off by default. Produces different P&L than immediate entry — useful for measuring timing cost.
- **XAU/USD is the primary instrument**: Out-of-sample testing (2024) showed EUR/USD PF 1.17 (too thin for live) vs XAU/USD PF 1.89. Gold's trending nature suits trend_follow. Focus live efforts on XAU/USD.
- **Optimized CobanReborn params**: SMA 3/7, MACD 8/21/5, ATR TP=3.0/SL=2.0 outperforms defaults (4/9, 12/26/9, 2.0/1.5) by +30%. Validated on 5 instruments with 6 seeds. See `scripts/money_maker_sweep.py` and `scripts/stress_test.py`.
- **Broker for live**: Interactive Brokers is the only broker with a NautilusTrader adapter. Paper trading available with $1M virtual, no expiry.
- **CobanReborn defaults**: `entry_mode="trend_follow"`, `exit_mode="atr"`, 24h trading (`session_start_hour=0`, `session_end_hour=24`). Use `-p session_start_hour=8 -p session_end_hour=17` for EUR/GBP. The old `"full"` mode requires 5-layer confluence (often 0 trades). The `-p` CLI flag parses `true`/`false` as strings, not booleans — use `entry_mode=trend_follow` not boolean params via CLI.
- **Non-FX instruments**: `TestInstrumentProvider.default_fx_ccy()` creates any pair with FX-style 5-decimal precision. For gold (XAU/USD ~$3000) and oil, the pip size (0.0001) is unrealistic — use ATR-based exits which auto-adapt to volatility. Fixed pip TP/SL need scaling (e.g., 300 pips for gold vs 10 for EUR/USD).
- **Dukascopy CLI flags**: Use `--date-from`/`--date-to` (not `-s`/`-e`), `-t m1` for timeframe, `-v` for volumes, `-f csv` for format, `--directory .` to save in current dir. Instrument names: `eurusd`, `usdjpy`, `gbpusd`, `xauusd`, `lightcmdusd` (WTI), `usdchf`, `eurgbp`. `bcousd`/`brentcmdusd` returns empty data. `audusd`/`nzdusd` may fail (IP blocking — use VPN).
- **Sweep scripts**: `scripts/coban_sweep.py` runs 10 entry/exit variations on EUR/USD. `scripts/coban_multi_pair.py` runs top variations across 5 instruments. `scripts/param_sensitivity.py` perturbs 7 key parameters +/- 40%. `scripts/walk_forward.py` runs rolling 3-month window analysis. All import `run_backtest` directly — run with `uv run python scripts/<name>.py`.

## Security

- No hardcoded API tokens or secrets — use `PYFX_*` env vars
- Django secret key must be set via `PYFX_SECRET_KEY` in production
- Validate user-provided file paths (no path traversal via `--data-file`)
- Timeouts on all HTTP requests
- No bare `except:` — always catch specific exceptions
- No `eval`/`exec`/`pickle` on untrusted data
- Dashboard views should use `login_required` when auth is added
