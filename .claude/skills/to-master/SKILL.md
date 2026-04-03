---
name: to-master
description: Finalize worktree work — run quality checks, update CLAUDE.md if needed, commit, merge to master, and report any post-merge steps.
user_invocable: true
---

Finalize the current worktree branch and merge it into master. Execute each step in order. Stop immediately if any step fails.

## Prerequisites

- You must be in a worktree (branch should be `claude/*`). If on `master` already, abort with an explanation.
- There must be uncommitted changes or commits ahead of `master`. If there's nothing to merge, say so and stop.

## Step 1 — Stage changes

Review all modified and untracked files with `git status` and `git diff`. Stage the relevant files. Do NOT stage:
- `.env`, credentials, secrets
- Large binary/data files (`.parquet`, `.h5`, `.sqlite3`)
- `logs/`, `__pycache__/`, `.mypy_cache/`

## Step 2 — Update CLAUDE.md if needed

Check whether any changes in this session affect the project's development workflow, gotchas, or patterns documented in `CLAUDE.md`. If so, update it and stage the changes. Common triggers:
- New CLI commands or skills added
- New dependencies or tools
- Changed dev setup steps
- New gotchas discovered

## Step 3 — Update skills if needed

Check whether any changes affect existing skills (e.g. `/checking-quality`). If a new command, tool, or workflow was added that an existing skill should know about, update it. If a new skill should be created for a repeatable workflow introduced in this session, create it.

Stage any skill changes.

## Step 4 — Quality checks

**Skip if already run:** If lint, types, tests, and coverage all passed earlier in this same session (e.g. the user already ran `/checking-quality` or ran them manually), skip this step entirely — just note "Quality checks already passed this session" and continue to Step 5.

Otherwise, run lint, type check, and tests inline (do NOT invoke `/checking-quality` — that's a separate skill invocation which interrupts this flow):

```bash
ruff check pyfx/
```

```bash
mypy pyfx/
```

```bash
pytest --cov=pyfx --cov-report=term-missing --cov-fail-under=100 tests/
```

All must pass. If anything fails, fix it before proceeding. Do NOT stop after this step — continue immediately to Step 5.

## Step 5 — Commit

Create a commit with a concise message summarizing the changes. Use imperative mood, ~60 chars, no prefix.

## Step 6 — Merge to master

You cannot `git checkout master` from inside a worktree because master is already checked out in the main repo. Instead, merge from the main repo directory:

```bash
cd /Users/joseph/Coding/private/pyfx-cli && git merge <branch-name> --no-edit
```

If the merge has conflicts, stop and report them — do not force-resolve.

## Step 7 — Post-merge checklist

Tell the user what they need to do after the merge. Check for:
- **New dependencies**: if `pyproject.toml` deps changed -> `uv sync --extra all`
- **New migrations**: if Django migration files were added -> `pyfx web` will auto-migrate, or run `uv run python -m django migrate --settings=pyfx.web.pyfx_web.settings`
- **New strategies**: if a strategy was added -> mention registering via entry points in `pyproject.toml` or placing in `strategies_dir`
- **Config changes**: if `pyfx/core/config.py` changed -> check `PYFX_*` env vars
- **Docker rebuild**: if `Dockerfile` or `docker-compose.yml` changed -> `docker compose build`

Print a clear checklist of only the applicable items. If nothing is needed, say "No post-merge steps required."
