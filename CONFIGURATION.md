# hamgoose â€” Configuration & Registration

## Config precedence (HG-08)

Config resolves in this order (later wins, per-key deep merge):

1. **factory defaults** (shown below)
2. **`HAMGOOSE_CONFIG`** env (JSON)
3. **`<repo>/.goose/hamgoose/config.json`** â€” per-repo defaults (same schema).
   This is the canonical channel: a value set here applies to **every** mission
   created in that repo, instead of one mission at a time. It is git-ignored
   along with the rest of `/.goose/hamgoose/`.
4. **`mission_create` `config` overrides** â€” per-mission (highest).

**One channel for every role (H5):** since 0.2.0 the mission's effective config
drives *all* semantic calls â€” worker dispatch, the validator, and the planner
alike. Editing a mission's config changes validation and planning behavior too,
not just workers. Unknown top-level config keys are **warned about at
`mission_create`** (the 0.1.8 `config.planner` silent drop is fixed) â€” see H2.

`mission_create` echoes the **effective** config (resolved values, never bare
`"inherit"`) so surprises are visible before approval. If a persisted mission's
config block (json **or** the human-readable `mission.yaml` mirror) disagrees
with what the controller is persisting, a `CONFIG_DRIFT` event is emitted
naming every changed key â€” hand edits stop being silently lost.

> Hand-editing `mission.yaml` is **not** a config channel (it is a derived
> mirror, never read back). Use the repo config file or `mission_create`
> overrides.

## Per-mission config

Pass `config` to `mission_create` (JSON object) or set `HAMGOOSE_CONFIG`
(JSON string). Defaults are shown; every field is overridable.

```json
{
  "orchestrator": { "provider": "inherit", "model": "inherit", "max_turns": 32 },
  "worker":       { "provider": "inherit", "model": "inherit", "max_turns": 32 },
  "validator":    { "provider": "inherit", "model": "inherit", "max_turns": 32 },
  "planner":      { "provider": "inherit", "model": "inherit", "max_turns": 32 },
  "execution":    { "max_concurrent_workers": 2, "max_feature_attempts": 3,
                    "worker_timeout": 900, "semantic_timeout": 600,
                    "planner_timeout": 600, "model_preflight": true,
                    "max_steps_per_run": 6 },
  "validation":   { "scrutiny": true, "user_testing": true, "max_correction_attempts": 3 },
  "git":          { "enabled": true, "use_worktrees": true,
                    "auto_commit_features": true, "base_branch": "mission/base",
                    "prefix": "mission" }
}
```

- **`provider`/`model`** per role. `"inherit"` falls back to the running Goose
  environment (`GOOSE_PROVIDER`/`GOOSE_MODEL`) / the host's active provider. Set
  e.g. `"provider": "custom_airouter", "model": "Qwen3.8"` to pin a role to a
  specific model. **`planner` is a first-class role since 0.2.0 (H2)**: it
  resolves per-field planner â†’ orchestrator â†’ environment, so pinning only the
  planner no longer requires pinning the orchestrator. `missionStatus`'s
  effective-config dump shows all three roles and warns when they diverge.
- **`max_concurrent_workers`** (default **2**): the concurrency ceiling. Two is the
  default so the system behaves well with providers that limit concurrent
  requests. Never exceeded; conflicting features are additionally serialized.
- **`max_feature_attempts`** (default **3**): bounded retries. Never retries
  forever. **Manual retries count** toward the same budget
  (`attempts + manual_retries >= max_attempts` stops further scheduling) and the
  `FEATURE_RETRIED` event says `beyond_budget` when it does — see
  `MISSION-LIFECYCLE.md`.
- **`worker_timeout`** (default **900 seconds**, H3): kills a stuck worker
  (`WORKER_TIMEOUT`) instead of holding the host open indefinitely. Raised from
  420 s because small-output-budget models (Qwen3.8-class) need more wall time
  for multi-file features; for faster models the cap is inert (a run ends when
  the model finishes, not at the cap). A run that finishes within a **10 s
  wall-clock grace** of the budget is still classified `WORKER_TIMEOUT`, not an
  implementation failure (the 420.8 s edge case). The model preflight suggests
  `â‰¥ 900` when it flags a SMALL-OUTPUT-BUDGET model; apply with
  `mission_apply_suggestions`.
- **`semantic_timeout`** (default **600 seconds**, H4): bounds the validator /
  diagnosis Goose calls. Raised from 180 s after a kill mid-verdict was
  misread as a quality failure and false-blocked a green milestone (MS01).
  **A validator timeout is infrastructure, not a verdict**: it is recorded with
  `timed_out: true`, never counted against `max_correction_attempts`, retried
  once at **double** the budget, and only then blocks the mission with a
  `validator timeout` reason. A failing verdict with **zero actionable
  findings** gets the same inconclusive-retry treatment instead of burning
  no-op corrective cycles.
- **`planner_timeout`** (default **600 seconds**): the planner's *own* budget
  (HG-06). Decomposition reads the whole repo analysis and must not share the
  short validator timeout. On timeout the planner retries once on a **smaller
  repo slice** (top-level tree + README head) before failing â€” and every planner
  exit emits an event with `timed_out` + redacted raw tail, so no silent planner
  death is possible.
- **`model_preflight`** (default **true**): at `mission_create`, run one bounded
  smoke leaf (â‰¤ 60 s, `--max-turns 2`) on the *resolved* worker model and record
  `repo_analysis.model_check` + a `Worker model:` readiness line
  (`smoke OK` / `SMALL-OUTPUT-BUDGET` / `WARN`). It **reports only** â€” it never
  switches models. The line is visible in `mission_status` before you approve.
  Set `false` for fully-offline / deterministic environments.
- **`max_steps_per_run`** (default **6**): keeps each `mission_run` call short;
  call it again to continue.
- **`max_turns`** (default **32** per role): bounds each isolated Goose task. Raise
  it for unusually complex features, or lower it for an even faster profile.
- **`validation.scrutiny` / `user_testing`**: enable/disable each validator.
- **`git.*`**: `enabled=false` runs features directly in the repo (no worktrees);
  `use_worktrees` toggles isolated worktrees; `auto_commit_features` toggles the
  per-feature commit.

## Tool-layer guarantees (H1) & recovery (H7)

- **No silent no-ops.** Every mutating tool ends its response with a
  `STATE:` proof line (freshly re-read status, feature counts, last event) and
  every failure returns a `TOOL_ERROR: <Exception>: <detail>` payload plus that
  same proof â€” a tool failure can no longer masquerade as an empty success.
  Tool errors on existing missions are also recorded as `MISSION_TOOL_ERROR`
  events.
- **Argument tolerance.** `features` / `milestones` / `changed_files` / `tests`
  accept native lists *or* JSON strings.
- **`mission_run` reporting (H6).** The response ends with a `RUN REPORT:`
  (dispatches this call, ready/queued features). `max_steps` counts
  dispatches; if a client sandbox times the call out, the loop keeps running
  server-side â€” poll `mission_events` instead of re-issuing.
- **Envelope-failure recovery (H7).** When a worker commits real work but dies
  before emitting the final JSON envelope, the run is classified
  `ENVELOPE_FAILURE` (retryable; the retry prompt says "you already wrote the
  code â€” emit the envelope"). If the budget is exhausted while git evidence
  shows the work exists on the branch, the feature is **accepted on git
  evidence** (`FEATURE_COMPLETED` payload says so) and milestone scrutiny
  still gates quality.
- **Scratch hygiene (H9).** Workers are instructed never to create scratch
  files inside the repo; before the reconcile commit, root-level untracked
  junk (`_*`, `scratch*`, `*.tmp/.diff/.rej/.orig/.bak`) is removed from the
  feature worktree and listed in a `SCRATCH_CLEANED` event.
- **`mission_apply_suggestions(mission_id)` (H10).** Applies the config deltas
  recorded at create time when the model preflight flags the worker model.
- **`mission_gc(max_age_days=7, archive=false)` (H11).** Lists terminal and
  long-stale missions (`mission_list` entries carry `terminal`, `age_days`,
  `stale`); with `archive=true` non-terminal stale missions are cancelled
  (data and event history are kept).

## Registering the extension

All three paths below produce the same `extensions:` entry in Goose's
config.yaml â€” pick whichever you prefer:

1. **Goose's own menu (recommended)** â€” `goose configure` â†’ **Extensions â†’
   Add Extension** â†’ Type `STDIO`, Name `hamgoose`, Command `hamgoose` (or
   `<python> -m hamgoose`). Toggle or remove it from the same menu later.
2. **One command** â€” `hamgoose register`. Auto-detects the config path via
   `goose info`, merges atomically, keeps a `.bak` of the previous file.
   Flags: `--name <n>`, `--config <path>`, `--force`. Reverse with
   `hamgoose unregister`.
3. **Manual** â€” add the YAML below to config.yaml (`goose info` prints the
   path; on this machine `.../Block/goose/config/config.yaml`).

### Per run (no config change)

```bash
goose run -t "..." --with-extension "hamgoose:C:\abs\path\to\python -m hamgoose"
```

### The entry

```yaml
extensions:
  hamgoose:
    enabled: true
    type: stdio
    name: hamgoose
    description: Mission orchestration for Goose
    cmd: C:\abs\path\to\.venv\Scripts\python.exe
    args: [-m, hamgoose]
```

Prefer a command that is on PATH (`hamgoose`) when possible so the entry
stays portable. Restart Goose after changes.


## Environment variables

- `GOOSE_REPOSITORY` or `HAMGOOSE_REPO`: default repo when a tool is called
  without an explicit `repo` (resource URIs use this too, so a Goose session
  operates on the repo it was launched in).
- `HAMGOOSE_CONFIG`: JSON config applied as a base for every mission.
- `GOOSE_PROVIDER` / `GOOSE_MODEL`: fallback for `"inherit"`.

## Git-ignoring runtime state

Add to the target repo's `.gitignore` so runtime logs never enter the user's repo:

```
/.goose/hamgoose/
```

## Known limitations

- **Conflict retry**: on a merge conflict the branch/worktree are preserved and the
  feature re-runs with the conflict evidence; a worker that keeps making the same
  conflicting change safely **blocks** after `max_feature_attempts` (work is
  preserved, never clobbered). Real LLM workers typically resolve on re-run.
- **Conflict heuristic** uses declared `expected_paths` (exact or path-prefix
  overlap). A feature with no `expected_paths` is treated as non-conflicting.
- **Real-LLM tests** are slower and can be flaky; tag/skip with `-m "not realgoose"`.
- **Speed vs. depth**: the defaults favor focused, observable runs without
  removing retries or validation. The two milestone validators still run, but
  they run concurrently; increase the time and turn limits per mission when a
  feature needs deeper investigation.
- **mcp pin**: `mcp[cli]>=1.25.0,<2` matches the documented `FastMCP` model. A
  future Goose requiring mcp 2.x needs the mechanical `FastMCP`â†’`MCPServer` rename.
