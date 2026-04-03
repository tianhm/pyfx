# Research Journals

Each strategy gets a folder under `research/` containing:

```
research/
  <strategy_name>/
    journal.md          # Main research journal (structured + chronological log)
    sweeps/             # Raw sweep outputs, comparison tables (optional)
    notes/              # Ad-hoc analysis, scratch work (optional)
```

## Journal Format

Each `journal.md` follows a hybrid format:

1. **Header** — strategy name, status, one-line verdict
2. **Overview** — what the strategy does, key parameters, instruments tested
3. **Current Best Config** — the config that works right now (copy-paste ready)
4. **Key Findings** — permanent conclusions that survived testing
5. **Research Log** — reverse-chronological entries (newest first), each with:
   - Date and session context
   - What was tested and why
   - Results (tables, numbers)
   - Conclusions and next steps

The log section grows over time. The structured sections at the top get updated
to reflect the latest understanding.
