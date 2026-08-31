# hamgoose — Configuration & Registration

## Per-mission config

Pass `config` to `mission_create` (JSON object) or set `HAMGOOSE_CONFIG`
(JSON string). Defaults are shown; every field is overridable.

```json
{
  "orchestrator": { "provider": "inherit", "model": "inherit", "max_turns": 32 },
  "worker":       { "provider": "inherit", "model": "inherit", "max_turns": 32 },
  "validator":    { "provider": "inherit", "model": "inherit", "max_turns": 32 },
  "execution":    { "max_concurrent_workers": 2, "max_feature_attempts": 3,
                    "worker_timeout": 420, "semantic_timeout": 180,
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
  specific model (worker and validator are configured independently).
- **`max_concurrent_workers`** (default **2**): the concurrency ceiling. Two is the
  default so the system behaves well with providers that limit concurrent
  requests. Never exceeded; conflicting features are additionally serialized.
- **`max_feature_attempts`** (default **3**): bounded retries. Never retries forever.
- **`worker_timeout`** (default **420 seconds**): kills a stuck worker
  (`WORKER_TIMEOUT`) instead of holding the host open indefinitely.
- **`semantic_timeout`** (default **180 seconds**): bounds planning and validation
  Goose calls.
- **`max_steps_per_run`** (default **6**): keeps each `mission_run` call short;
  call it again to continue.
- **`max_turns`** (default **32** per role): bounds each isolated Goose task. Raise
  it for unusually complex features, or lower it for an even faster profile.
- **`validation.scrutiny` / `user_testing`**: enable/disable each validator.
- **`git.*`**: `enabled=false` runs features directly in the repo (no worktrees);
  `use_worktrees` toggles isolated worktrees; `auto_commit_features` toggles the
  per-feature commit.

## Registering the extension

All three paths below produce the same `extensions:` entry in Goose's
config.yaml — pick whichever you prefer:

1. **Goose's own menu (recommended)** — `goose configure` → **Extensions →
   Add Extension** → Type `STDIO`, Name `hamgoose`, Command `hamgoose` (or
   `<python> -m hamgoose`). Toggle or remove it from the same menu later.
2. **One command** — `hamgoose register`. Auto-detects the config path via
   `goose info`, merges atomically, keeps a `.bak` of the previous file.
   Flags: `--name <n>`, `--config <path>`, `--force`. Reverse with
   `hamgoose unregister`.
3. **Manual** — add the YAML below to config.yaml (`goose info` prints the
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
  future Goose requiring mcp 2.x needs the mechanical `FastMCP`→`MCPServer` rename.
