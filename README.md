# pyfx

Backtesting and live trading tool for forex and other instruments, powered by [NautilusTrader](https://nautilustrader.io/).

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/getting-started/installation/)

## Installation

```bash
git clone <repo-url> && cd pyfx-cli

uv sync --all-extras        # install everything (core + web + dev)
```

Install only what you need:

```bash
uv sync                     # core only (backtest + CLI)
uv sync --extra web         # + Django dashboard
uv sync --extra dev         # + pytest, ruff, mypy
```

## Quick Start

```bash
# Generate synthetic sample data
uv run pyfx generate-sample-data

# Run a backtest
uv run pyfx backtest -s sample_sma --start 2024-01-01 --end 2024-06-01 --data-file ~/.pyfx/data/sample.csv

# List available strategies
uv run pyfx strategies

# Launch the web dashboard (requires the web extra)
uv run pyfx web
```

## Web Dashboard

The `pyfx web` command auto-migrates the database and starts a Django dev server:

```bash
uv run pyfx web                              # http://127.0.0.1:8000/
uv run pyfx web --host 0.0.0.0 --port 9000   # custom host/port
```

Requires the web extra (`uv sync --extra web`).

### Django Management Commands

There is no `manage.py`. To run Django commands directly (e.g. `createsuperuser`, `shell`, `showmigrations`):

```bash
DJANGO_SETTINGS_MODULE=pyfx.web.pyfx_web.settings uv run django-admin <command>
```

The database is SQLite at `~/.pyfx/db.sqlite3` (configurable via `PYFX_DB_PATH`).

## Configuration

All settings use the `PYFX_` prefix and can be set via environment variables or a `.env` file.

| Variable | Description | Default |
|----------|-------------|---------|
| `PYFX_DATA_DIR` | Local data cache | `~/.pyfx/data` |
| `PYFX_CATALOG_DIR` | NautilusTrader Parquet catalog | `~/.pyfx/catalog` |
| `PYFX_STRATEGIES_DIR` | Extra directory to scan for strategies | None |
| `PYFX_DEFAULT_BALANCE` | Starting balance (USD) | 100,000 |
| `PYFX_DEFAULT_LEVERAGE` | Leverage ratio | 50 |
| `PYFX_DB_PATH` | SQLite database path | `~/.pyfx/db.sqlite3` |
| `PYFX_SECRET_KEY` | Django secret key | dev default |

## Writing a Strategy

1. Create a file in `pyfx/strategies/`
2. Extend `PyfxStrategy` from `pyfx.strategies.base`
3. Implement `on_start()` and `on_bar(bar)`
4. Register via entry points in `pyproject.toml` or place the file in `PYFX_STRATEGIES_DIR`

Convenience methods: `market_buy()`, `market_sell()`, `close_all()`, `flat()`, `is_long()`, `is_short()`.

## Development

```bash
uv run ruff check pyfx/                                                         # lint
uv run mypy pyfx/                                                               # type check
uv run pytest --cov=pyfx --cov-report=term-missing --cov-fail-under=100 tests/  # tests (100% coverage required)
```
