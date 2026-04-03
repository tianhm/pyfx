---
name: checking-quality
description: Runs lint, type check, security/best-practices review, and tests with 100% coverage for pyfx. Use before committing, after code changes, or when verifying project health.
user_invocable: true
---

Run a full pyfx health check. Execute each step in order and report results.

## Step 1 — Lint

```bash
ruff check pyfx/
```

Run from the project root. Any output = failure. Report errors with file:line references.

## Step 2 — Type check

```bash
mypy pyfx/
```

Strict mode is configured in `pyproject.toml`. Zero errors required.

## Step 3 — Security & best practices review

Review all changed files (`git diff` + `git diff --cached` + untracked) for:

- **Security**: hardcoded secrets (API keys, Django secret key, tokens), missing request timeouts, unsafe deserialization (`pickle`, `eval`, `exec`), path traversal in data file loading, SQL injection via raw queries or unsafe ORM usage, XSS in Django templates (`|safe` misuse), missing CSRF protection in dashboard forms, secrets leaking into logs or CLI output
- **Django-specific**: `DEBUG = True` in production settings, missing `login_required` on dashboard views, `ALLOWED_HOSTS` misconfiguration
- **Best practices**: bare `except:` or `except Exception: pass`, business logic in `cli.py` (should be in `backtest/`, `strategies/`, or `core/`), missing type hints (mypy strict), Decimal precision not enforced at system boundaries

Only flag concrete issues in the changed code — not hypothetical concerns or style preferences (lint handles style). Report each finding with file:line and severity (critical / warning).

## Step 4 — Tests

```bash
pytest --cov=pyfx --cov-report=term-missing --cov-fail-under=100 tests/
```

All tests must pass. 100% line coverage required. Report any failures or uncovered lines.

## Step 5 — Summary

Print a clear summary:
- Pass or fail for each step (lint, types, security, tests)
- Total test count and coverage %
- Type error count
- Security findings count (critical / warning)
- Any action required

If everything passes, say so. If anything fails, stop and fix before committing.

## Notes

- Ruff config is in `pyproject.toml` — line length 100, rules: E, F, I, UP, target py312
- MyPy config is in `pyproject.toml` — strict mode, py312
- Coverage config is in `pyproject.toml` under `[tool.coverage.*]`
