# hamgoose — Testing

## Running

```bash
uv pip install -p .venv "pytest>=8" "pytest-asyncio>=0.23"

# Fast deterministic suite (no LLM): unit + orchestration integration A–L
.venv\Scripts\python -m pytest -m "not realgoose" -q

# Real Goose + LLM integration (slower; skipped if goose is not on PATH)
.venv\Scripts\python -m pytest -m "realgoose" -q
```

> Windows note: if pytest cannot create its default per-user temp dir under
> `%TEMP%` (e.g. `PermissionError: [WinError 5] … pytest-of-<user>`), point
> basetemp at a repo-local (git-ignored) directory:
> `-p no:cacheprovider --basetemp=.pytest_tmp`

## What is tested

### Unit (`tests/test_*.py`)
- **State machines** (`test_state.py`): legal transitions; illegal ones raise
  (cannot resume a completed mission, cannot re-open a passed milestone).
- **Scheduler** (`test_scheduler.py`): readiness gated on `COMPLETED` deps,
  conflict serialization, concurrency ceiling, priority ordering, cycle detection.
- **Plan validation** (`test_plan.py`): cycles, self/dangling deps, vague &
  micro-features; best-effort fixes.
- **Store** (`test_store.py`): atomic save/load round-trip, human mirrors,
  append-only events.
- **Redaction** (`test_redact.py`): bearer/api-key/sk-/AWS secrets scrubbed.
- **Models** (`test_models.py`): (de)serialization round-trips.

### Integration — full orchestration path (`tests/integration/test_lifecycle.py`)

Runs the **real** `MissionController` with deterministic mock backends (no LLM),
covering the required scenarios:

| ID | Scenario | Verified |
|---|---|---|
| A | Planning gate | plan created & persisted; **no implementation before approval** |
| B | Dependencies | `F003` cannot run before `F001` |
| C | Parallel workers | independent features run concurrently; ceiling ≤ 2 never exceeded |
| D | Worker failure | failure recorded, attempt incremented, retry recovers, state valid |
| E | Validator defect | validator rejects placeholder, **corrective feature** created & run, re-validated |
| F | Pause/resume | no new workers while paused; state persists; resumes correctly |
| G | Crash recovery | feature left `RUNNING` on disk is reconciled to `READY` and completes |
| H | Steering | scheduler honors updated priorities |
| I | Replanning | execution pauses, valid work preserved, invalid → `SUPERSEDED`, history kept |
| J | Git conflict | conflict **detected**, branch preserved, **not clobbered** |
| K | Final validation | not `COMPLETED` until final validation passes |
| L | Nested delegation | worker prompt forbids delegation; backend is a single `goose run` |

### Real-Goose integration (`tests/integration/test_real_goose.py`, tag `realgoose`)

Drives the **actual** Goose path (requirement 32):
1. **Server starts & is discoverable** over the real stdio MCP transport
   (tools/prompts/resource templates listed via an MCP client).
2. **A real isolated `goose run` worker** (GooseRunBackend) makes a **real change
   to a real git repository** and the mission completes.
3. **Mission state survives a restart** (a fresh controller reads the disk state).
4. **Goose discovers & drives hamgoose** via `--with-extension` and a real
   `mission_create` tool call produces a mission on disk (asserted on the
   deterministic side effect, not the LLM's phrasing).

These are skipped automatically when `goose` is not on PATH and are tagged so the
fast suite runs without an LLM.

## Design note

The deterministic mock backends prove the **orchestration mechanics** (scheduling,
persistence, validation loop, recovery, git) are correct and repeatable. The
`realgoose` suite proves the **integration** with actual Goose and a live model.
Together they satisfy "do not consider standalone MCP-server testing sufficient."
