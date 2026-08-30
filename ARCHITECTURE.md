# hamgoose — Architecture

## Guiding principle

**Prompts express semantic intent. Code enforces orchestration mechanics.**

Models decide *how* to decompose/implement/validate/replan. The extension code
decides *ids, state transitions, dependency satisfaction, concurrency, retries,
timestamps, persistence, worker/process tracking, Git/worktree tracking, event
logging, pause/timeout, crash recovery, and operation legality*. Deterministic
Mission state never lives in conversational memory.

## Component diagram

```
                        Goose
                          |
                          | MCP (stdio)
                          v
                       hamgoose  (FastMCP server: server.py)
        tools / resources / prompts
                          |
                          v
                  MissionController (controller.py)   <-- the orchestrator
   |         |         |          |          |           |
   v         v         v          v          v           v
 models/   state.py  scheduler  git.py    worker.py   validator.py
 (data)   (FSM)       (DAG)     (Git/wt)  (isolated  (scrutiny/
                                    goose run) user-test)
        +  semantic.py (goose run / sampling for planning, diagnosis, replan)
        +  store.py (atomic persistence + events.jsonl)  +  plan.py (plan check,
           readiness)  +  redact.py (secrets)  +  render.py (human output)
```

## The orchestrator (MissionController)

Owns the control loop. It does **not** implement features itself; it schedules
and supervises. One `advance`/step:

1. **Reconcile** repository + worker reality (reset stale `RUNNING` → `READY`,
   prune orphan worktrees with no commit).
2. **Process** the active milestone: if all its features are terminal, run
   **validators**; on pass → `PASSED` and advance; on fail → create **corrective
   features** (bounded), re-run; if exhausted → `BLOCKED`.
3. **Schedule** dependency-ready, non-conflicting features up to
   `max_concurrent_workers`.
4. **Dispatch** the batch as parallel isolated `goose run` subprocesses in
   worktrees; then **reconcile** each result against the *actual* repo (commits,
   diffs, merge conflicts), classify failures, and transition features.
5. **Persist** state (atomic) + append events, then return a stable signal.

`mission_run` calls the step repeatedly up to a checkpoint budget, so execution
is resumable and survives restarts.

## Isolation model

- **Workers** are separate `goose run` processes (`--no-session`), each in its own
  Git worktree, individually identifiable (run_id/pid/provider/model), with the
  transcript captured and redacted. Workers are **leaf**: the prompt forbids
  delegation and the backend issues a single `goose run` (no nested delegation).
- **Validators** are fresh isolated `goose run` contexts that distrust worker
  claims and inspect the real repository.
- **Semantic** tasks (plan, diagnosis, replan) run in isolated `goose run` (or,
  optionally, MCP sampling).

## Git as source of truth

On approve: create `mission/base` at the base commit + a base worktree. Each
feature: worktree on branch `mission-<F>` from `mission/base`. On success:
commit in the worktree, merge into `mission/base`; **conflicts are detected and
preserved, never clobbered**. The user's branch is untouched; `mission/base` holds
the merged validated result.

## Backends (swap for tests)

`WorkerBackend`, `ValidationBackend`, `SemanticClient` are injectable. Production
uses `GooseRunBackend` / `GooseRunValidationBackend` / `SemanticClient(goose_run)`.
Tests inject deterministic `MockBackend` / `MockValidationBackend` to exercise the
entire orchestration path without an LLM, and a tagged suite exercises the real
backends against real Goose.

## Failure classification

`MODEL_FAILURE, PROVIDER_FAILURE, WORKER_TIMEOUT, WORKER_CRASH,
IMPLEMENTATION_FAILURE, TEST_FAILURE, VALIDATION_FAILURE, MERGE_CONFLICT,
DEPENDENCY_FAILURE, USER_BLOCKED, INFRASTRUCTURE_FAILURE`. Retryable classes
retry with changed strategy (prior evidence injected) up to `max_feature_attempts`;
`USER_BLOCKED` and unrecoverable conflicts `BLOCK` with a precise reason.
