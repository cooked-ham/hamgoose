# HAMGOOSE ARCHITECTURE REPORT

Produced after reading the official Goose custom-extension documentation and
inspecting the installed Goose environment, per the coding plan.

## 1. Official Goose extension documentation findings

Source: https://goose-docs.ai/docs/tutorials/custom-extensions (read in full).

Current official model (Goose 1.48.0): a custom extension is a **standalone MCP
server** (stdio or streamable-HTTP) written in Python with the `mcp` package
(`FastMCP`). It is a normal Python project:

```
├── pyproject.toml        # [project.scripts] entry point, deps: mcp[cli]>=1.25.0
└── src/<pkg>/
    ├── __init__.py       # main() CLI entry point
    ├── __main__.py       # `python -m <pkg>`
    └── server.py         # FastMCP server: tools, resources, prompts
```

- Server code uses `FastMCP("<name>")` and decorators `@mcp.tool()`,
  `@mcp.resource("uri")`, `@mcp.prompt()`.
- MCP Sampling: servers may request host-LLM completions
  (`sampling/createMessage`); Goose advertises the capability to all MCP servers.
- MCP Apps: tools may return interactive UI (optional; not required here).
- Integration: Desktop (Type=STDIO, Command=absolute path to the executable) or
  CLI `--with-extension`. After code changes: `uv pip install .` and restart.
- MCP 2.x renamed `FastMCP`→`MCPServer`; the documented examples and Goose tooling
  target **mcp 1.x `FastMCP`**, so hamgoose pins `mcp[cli]>=1.25.0,<2`.

## 2. Installed Goose version / environment

- `goose` 1.48.0 at `C:\Users\Sean\.local\bin\goose.exe`.
- Config: `C:\Users\Sean\AppData\Roaming\Block\goose\config\config.yaml`.
- Sessions DB: `.../Block/goose/data/sessions/sessions.db`.
- Active provider: `custom_airouter` / model `Qwen3.8` (configured & working;
  verified with a live `goose run`). `zai`/`zhipu` (glm-5.3-flash) also configured.
- Python 3.13/3.14 available; `uv` 0.12.7; `git` present.
- `goose run` headless flags confirmed: `-t/--text`, `-i` (instructions file),
  `--provider`, `--model`, `--max-turns`, `--output-format {text,json,stream-json}`,
  `--no-session`, `--with-extension`, `--with-builtin`, `--resume/--session-id`.
  `goose run --output-format json` emits `{messages:[{role,content:[{type:text,text}]}], metadata}`.

## 3. Supported MCP capabilities

Tools (state-changing), Resources (read-oriented data, URIs with `{param}`
templates), Prompts (reusable workflows), Sampling (host-LLM completion).
All verified live via an MCP client round-trip (16 tools, 4 prompts, 6 resource
templates).

## 4. Extension registration mechanism

stdio extension in `config.yaml` under `extensions:` (or `--with-extension` per
run). Provided example in `README.md`. The extension is stateless; all state is
on disk, so it reconnects cleanly after a Goose restart.

## 5. Transport choice

**stdio.** Simplest, officially documented, works for Desktop and CLI, no network
surface, per-session isolation.

## 6. SDK / runtime choice

Python 3.11+, `mcp[cli]>=1.25.0,<2` (`FastMCP`), `pydantic` (config), `pyyaml`
(human-readable mirrors). Packaged with hatchling; entry point `hamgoose`.

## 7. Tools / Resources / Prompts design

Tools (state-changing): `mission_create, mission_plan, mission_approve,
mission_run, mission_pause, mission_resume, mission_steer, mission_replan,
mission_cancel, mission_retry_feature, mission_validate` + read tools
`mission_status, mission_plan_view, mission_readiness, mission_list,
mission_events`.
Resources (read): `mission://{id}/status|plan|events|features|milestones|validation`.
Prompts: `start_mission, plan_mission, resume_mission, validate_milestone`.

## 8. MCP Sampling decision

**Default = isolated `goose run` for semantic tasks** (decomposition, diagnosis,
replanning, validation verdicts). Sampling is implemented as an optional backend
(`SemanticClient(backend="sampling", sampler=...)`) but is **off by default**
because: (a) `goose run` is reliable, isolated, headless, and works identically in
Desktop and CLI; (b) sampling couples the extension to a live host session and an
event loop, complicating the deterministic control loop. This is the cleanest and
most reliable design; sampling remains available for hosts that prefer it.

## 9. Worker isolation mechanism

Each worker is a **separate `goose run` subprocess** (its own process, `--no-session`
isolated context) run in the feature's git worktree. Workers are individually
identifiable (`run_id`, `pid`, provider/model recorded), observable (full transcript
captured + redacted to `workers/`), cancellable/timeout-able, and **leaf** (the worker
prompt forbids delegation; the backend issues a single `goose run` with no
`--with-extension`/delegate). No nested delegation, no simulated in-context workers.

## 10. Delegation capabilities & limitations

Goose `delegate`/`summon` exist but are **not used** for workers (leaf constraint).
Workers are managed processes, not subagents. The orchestrator (the extension code)
does all scheduling; it does not rely on conversational memory.

## 11. Headless Goose capabilities

`goose run` headless with `--output-format json`, `--provider/--model`,
`--max-turns`, `--no-session` — used for workers and semantic tasks. Verified live.

## 12. Session persistence behavior

Workers use `--no-session` (truly isolated, no session pollution). Mission state
is independent of Goose sessions and survives Goose/machine restarts (on disk).

## 13. Mission persistence architecture

`<repo>/.goose/hamgoose/<mission-id>/`: `mission.json` (canonical, **atomic**
write-tmp + `os.replace`), `mission.yaml` + `plan.md` (human mirrors),
`events.jsonl` (append-only event log), `artifacts/`, `workers/`, `validation/`,
`worktrees_base/` + `worktrees/<feature>/`. A single atomic JSON is the source of
truth (chosen over SQLite to avoid dual-source inconsistency at this scale; the
append-only `events.jsonl` supports full state reconstruction/debugging).

## 14. Git / worktree strategy

Git is the source of truth. On approve: create `mission/base` at the base commit and
a base worktree. Each feature gets an isolated worktree on branch `mission-<feature>`
branched from `mission/base`. On success the branch is committed and merged into
`mission/base` (conflicts detected, never clobbered). The user's current branch is
never touched; `mission/base` accumulates the merged result for the user to merge.
If Git is disabled, features run in the repo with disjoint expected-paths.

## 15. Scheduler architecture

Dependency DAG. A feature is `READY` only when every dependency is `COMPLETED`.
`select_batch` sorts by priority, skips features whose `expected_paths` overlap an
already-selected member (so conflicting work is sequential), and caps the batch at
`max_concurrent_workers` (default **2**). Batches run in a thread pool (parallel
subprocesses); merges are serialized.

## 16. Validator architecture

Two roles in fresh isolated `goose run` contexts: **Scrutiny** (distrusts worker
claims; inspects diff/code/tests) and **User-testing** (exercises the app). Each
returns structured JSON `{passed, severity, findings[]}`. Both independently
enable/disable-able. **Final validation** gates mission completion.

## 17. Crash-recovery architecture

On `run`/`resume`, `_reconcile` resets any feature left `RUNNING` (no live process)
to `READY`, prunes orphan worktrees with no commit, and continues from the last
persisted (atomic) state. Every control-loop step persists before returning.

## 18. Security / permission implications

Respects Goose's model (workers run as normal `goose run` with normal permissions;
repo boundary is the workdir). No secrets persisted: worker transcripts and
validation output are **redacted** (bearer/api-key/sk-/AWS/URI-credentials).
Workers receive only necessary context (scoping, prohibited paths).

## 19. Components / files created

`server.py` (MCP), `controller.py` (orchestrator), `models.py`, `state.py`,
`scheduler.py`, `worker.py`, `validator.py`, `semantic.py`, `git.py`, `store.py`,
`config.py`, `plan.py`, `render.py`, `prompting.py`, `redact.py`, `ids.py`.
Tests: unit + integration (A–L) + real-Goose suite. Docs: README, ARCHITECTURE,
MISSION-LIFECYCLE, CONFIGURATION, TESTING.

## 20. Integration-test strategy

Deterministic mock backends drive the full orchestration path (scenarios A–L)
without an LLM; a tagged `realgoose` suite drives the **real** path (stdio MCP
discovery, real `goose run` worker modifying a real git repo, state surviving
restart, Goose discovering/driving hamgoose via `--with-extension`).

## 21. Known risks / limitations

- Conflict retry re-runs the worker; a worker that always makes the same conflicting
  change safely **blocks** after bounded attempts (work preserved, not clobbered).
- Path-overlap conflict heuristic uses declared `expected_paths`; unknown scope is
  assumed non-conflicting.
- Real-LLM tests are slower and can be flaky; they are tagged and skippable
  (`-m "not realgoose"`).
- `mcp` pinned `<2` to match documented `FastMCP`; a future Goose that requires
  mcp 2.x would need the `MCPServer` rename (mechanical).

---
## OFFICIAL GOOSE EXTENSION COMPLIANCE

**PASS** — hamgoose is a standalone stdio MCP server on the documented Python
`mcp[cli]`/`FastMCP` layout, registered as a Goose extension, using MCP Tools,
Resources, Prompts, and (optionally) Sampling. No Goose core modification.

### Deviations

- Worker/semantic execution uses `goose run` subprocesses rather than in-process
  delegation: a deliberate, documented choice for isolation, reliability and
  headless operation (allowed — it is Goose's own supported headless interface).
- Persistence uses an atomic JSON + append-only JSONL rather than SQLite: a
  documented structural choice ("exact structure may differ").

### Implementation may proceed: **YES**
