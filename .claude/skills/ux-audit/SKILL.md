---
name: ux-audit
description: UX audit for pyfx CLI and Django dashboard. Checks CLI ergonomics, output readability, configuration UX, and dashboard usability.
user_invocable: true
---

# UX Audit

User experience review for pyfx. Covers both the CLI interface and the optional Django dashboard.

## Arguments

The user may specify a focus (e.g., `/ux-audit cli`, `/ux-audit dashboard`, `/ux-audit backtest`). If none specified, audit everything.

---

## How to Audit

1. Read `pyfx/cli.py` for all CLI commands and their options
2. Read `pyfx/core/config.py` for configuration UX
3. Read `pyfx/web/dashboard/` for dashboard views and templates
4. Run commands to see actual output behavior
5. Fix issues directly, don't just report them
6. Run `/checking-quality` when done

---

## CLI UX Checklist

### Help & Discoverability
- All commands have clear `help=` docstrings
- All options have `help=` text that explains what they do
- Required options are marked `required=True` (not silently defaulted)
- `pyfx --help` gives a clear overview of available commands
- `pyfx <command> --help` shows all options with useful descriptions
- Related options are grouped logically

### Error Messages
- Missing required options produce clear errors with hints (not bare tracebacks)
- Invalid data files show actionable errors ("file not found" with the path, "no data in range" with the range)
- Missing dependencies show how to install them (e.g., `uv sync --extra web` for dashboard)
- Configuration errors (bad env vars) produce Pydantic validation messages, not cryptic crashes

### Output Readability
- Backtest results are well-formatted: aligned columns, currency formatting (`$1,234.56`), percentages (`+12.34%`)
- Strategy list (`pyfx strategies`) shows name + description in aligned columns
- Progress feedback for long-running operations (bar count, elapsed time)
- Color/styling used where appropriate (success/failure indicators)
- No excessive output — default is concise, `--verbose` or `--log-level DEBUG` for detail

### Defaults & Convenience
- Sensible defaults for all optional params (balance, leverage, bar type, log level)
- Date formats are intuitive and well-documented
- `--param key=value` supports common types (int, float, string) automatically
- `generate-sample-data` creates data in a predictable location with a clear filename

---

## Configuration UX Checklist

- `PYFX_` prefix is consistent across all env vars
- `.env` file is supported and documented
- Missing required config (if any) produces clear Pydantic errors listing what's missing
- `~/.pyfx/` directories are auto-created when needed (data, catalog, db)
- Default values are sensible for getting started without configuration

---

## Dashboard UX Checklist

### Layout & Navigation
- Backtest list is scannable: key metrics visible at a glance (P&L, return %, trades, win rate)
- Backtest detail shows complete information: config, metrics, equity curve, trade list
- Navigation between list and detail is clear
- Delete action has confirmation

### Data Display
- Numbers formatted consistently: currency with `$`, percentages with `%`, large numbers with commas
- Dates in a readable format
- Equity curve chart is responsive and readable
- Trade table is sortable or at least well-ordered (by time)
- Empty states: clear message when no backtests exist yet

### Responsiveness
- Dashboard works on common screen sizes (no horizontal scroll on laptop screens)
- Tables have horizontal scroll wrapper if columns are wide
- Charts resize properly

---

## Output

Group findings by severity, fix directly:

1. **Critical** — broken commands, misleading output, crashes on valid input
2. **Usability** — confusing messages, missing help text, poor formatting
3. **Polish** — alignment, spacing, consistency improvements

Run `/checking-quality` when done.
