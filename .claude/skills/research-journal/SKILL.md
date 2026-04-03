---
name: research-journal
description: Update or create a strategy research journal after running backtests, sweeps, or experiments. Captures hypotheses, results, conclusions, and open questions so nothing is lost between sessions.
user_invocable: true
---

After a backtest session, update (or create) the research journal for the strategy that was tested. This skill should be invoked at the END of a research/testing session, not before.

## Step 1 — Identify what was tested

Review the current conversation to extract:
- **Strategy name** — which strategy was tested (e.g. `coban_reborn`, `sample_sma`)
- **What changed** — new entry/exit logic, parameter tweaks, new instruments, new timeframes
- **Why** — the hypothesis or motivation (e.g. "test if ATR exits outperform fixed pips")
- **Results** — trade counts, P&L, win rate, profit factor, max drawdown. Build a comparison table if multiple variations were tested
- **Conclusions** — what worked, what didn't, what was surprising
- **Open questions** — what to test next

## Step 2 — Find or create the journal

Check if `research/<strategy_name>/journal.md` exists.

**If it exists:** Read it, then proceed to Step 3 (update).

**If it doesn't exist:** Create the directory and journal using this template:

```markdown
# <StrategyName> Strategy Research Journal

**Status:** <Active | Experimental | Abandoned | Archived>
**Verdict:** <One-line summary of current understanding>

---

## Overview

<What the strategy does, 2-3 sentences. Key indicators, timeframes, instruments.>

**Source:** `pyfx/strategies/<file>.py`

---

## Current Best Config

```bash
<Copy-paste ready CLI command for the best-performing configuration>
```

**Key parameters:**
<Bullet list of the important config values and what they do>

---

## Key Findings

<Numbered list of permanent conclusions that survived testing. Start empty, grow over time.>

---

## Research Log

### <YYYY-MM-DD> — <Short title>

**Context:** <Why this test was run>

**What was tested:** <Description of variations, parameters, instruments>

**Results:**

| Variation | Trades | WR% | P&L | PF | MaxDD |
|-----------|--------|-----|-----|-----|-------|
| ... | ... | ... | ... | ... | ... |

**Conclusions:** <What we learned>

**Next steps:** <What to test next>

---

## Open Questions

- [ ] <Question 1>
- [ ] <Question 2>
```

## Step 3 — Update an existing journal

Make these updates in order:

### 3a. Add a Research Log entry

Prepend a new dated entry to the **Research Log** section (newest first). Each entry must include:

- **Date** — today's date (`YYYY-MM-DD`)
- **Title** — short description of what was tested (e.g. "ATR exit multiplier sweep")
- **Context** — why this was tested, what prompted it
- **What was tested** — specific variations, parameters, instruments, date ranges
- **Results** — markdown table with numeric results. Always include: Trades, Win Rate, P&L, Profit Factor, Max Drawdown. Add other columns as relevant.
- **Conclusions** — what worked, what didn't, what was surprising
- **Next steps** — what should be tested next (feeds into Open Questions)

### 3b. Update structured sections (if findings warrant it)

- **Status/Verdict** — update if the overall assessment changed
- **Current Best Config** — update if a new best config was found. Must be a copy-paste CLI command.
- **Key Findings** — add new findings only if they're durable conclusions (not one-off observations). Remove findings that were disproven.
- **Overview** — update if the strategy's capabilities changed (new modes, new instruments)

### 3c. Update Open Questions

- Check off questions that were answered by this session's work
- Add new questions raised by the results
- Remove questions that are no longer relevant

## Step 4 — Report

Tell the user what was updated with a brief summary:
- Which journal was updated (new or existing)
- How many log entries were added
- Any key findings added or updated
- Any open questions resolved or added

## Guidelines

- **Be specific with numbers.** Don't write "P&L improved" — write "+$5,682 vs +$3,585 baseline".
- **Include the test parameters.** Someone reading the journal months later needs to reproduce the test.
- **Tables over prose** for results. Easy to scan and compare.
- **Date everything.** The log is the historical record.
- **Don't duplicate code.** Reference file paths and CLI commands, don't paste strategy source.
- **Keep Key Findings short.** Each finding should be 1-2 sentences. The log has the details.
- **Current Best Config must be copy-paste ready.** Include the full CLI command with all flags.
