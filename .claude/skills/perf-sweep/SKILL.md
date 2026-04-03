---
name: perf-sweep
description: Codebase performance audit — NautilusTrader engine, data I/O, strategy execution, pandas vectorization, memory usage, and Django dashboard queries.
user_invocable: true
---

# Performance Sweep

Audit the pyfx codebase (or a specified module) for performance issues.

## Arguments

The user may specify a module (e.g., `/perf-sweep backtest`, `/perf-sweep strategies`). If none specified, audit the full codebase.

## Audit Areas

### 1. NautilusTrader Engine
- BacktestEngine configuration: are unnecessary features enabled?
- Bar data wrangling: is `BarDataWrangler` being used efficiently?
- Unnecessary data copies during engine setup
- Result extraction: are trades/equity snapshots being extracted optimally?

### 2. Data I/O
- CSV vs Parquet: is Parquet used where possible? (faster reads, smaller files)
- Unnecessary `pd.read_csv`/`pd.read_parquet` with full file when only a slice is needed
- DataFrame copies: unnecessary `.copy()` calls
- dtype optimization: float64 where float32 suffices, object columns that should be categorical
- Index operations: is `tz_localize` being called redundantly?

### 3. Pandas/NumPy Vectorization
- Row-by-row loops (`iterrows`, `apply`) that should be vectorized
- Indicator calculations: pandas-ta vs custom NumPy — is the faster option used?
- Redundant DataFrame column computations
- Unnecessary type conversions (e.g., repeated Decimal <-> float)

### 4. Strategy Tick Performance
- Expensive work in `on_bar()` that could be precomputed in `on_start()`
- Redundant indicator recalculations on every bar
- Heavy allocations inside the hot path
- Strategy state that grows unbounded over time

### 5. Memory Usage
- Long backtests: do lists/DataFrames grow without bounds?
- Equity curve snapshots: stored more frequently than needed?
- Trade records: are intermediate results held in memory unnecessarily?
- Large DataFrames not released after processing

### 6. Django Dashboard
- N+1 queries: views with loops that trigger separate queries per item
- Missing `select_related()` / `prefetch_related()` on ForeignKey access
- Large JSON responses for equity curves/trades — paginated or streamed?
- Unused fields loaded in querysets (use `.only()` or `.defer()`)

### 7. Redundant Code
- Duplicated logic across modules
- Dead code (unused imports, unreachable branches)
- Overly broad exception handling hiding performance issues

## Process

1. Read the relevant source files
2. Identify issues with severity: **Critical** (causes slowness) / **Important** (preventive) / **Minor** (cleanup)
3. Fix issues directly, starting with Critical
4. Run `/checking-quality` when done

## Gotchas

- NautilusTrader's internal engine is Rust-based and highly optimized — don't try to optimize inside the engine, optimize around it
- `pandas-ta` can be slow for some indicators — profile before replacing
- Django dashboard is optional (`[web]` extra) — don't break the core for dashboard perf
