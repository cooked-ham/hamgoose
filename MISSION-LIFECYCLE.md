# hamgoose — Mission Lifecycle

## Mission state machine

```
CREATED → ANALYZING → PLANNING → AWAITING_APPROVAL → RUNNING ⇄ PAUSED
                                   │                     │  ▲
                                   │                     ▼  │
                                   │                   BLOCKED
                                   ▼                     │
                              (replan → PLANNING)        │
                                   RUNNING → VALIDATING → COMPLETED
                                                     ↘ FAILED / CANCELLED
```

Transitions are **validated in code** (`state.py`). Illegal moves raise
`IllegalTransition` — e.g. you cannot `resume` a `COMPLETED` mission, or re-open a
`PASSED` milestone. Terminal states (`COMPLETED`, `FAILED`, `CANCELLED`) have no
outbound transitions.

## Feature states

`PENDING → READY → RUNNING → VERIFYING → COMPLETED`
with side states `NEEDS_FIX` (retryable failure), `FAILED` (retries exhausted),
`BLOCKED`, `CANCELLED`, `SUPERSEDED` (replan-invalidated).

A feature becomes `READY` only when **every** dependency is `COMPLETED`.

## Milestone states

`PENDING → RUNNING → VALIDATING → PASSED` | `FAILED` | `BLOCKED`.
Milestones are integration/validation boundaries. A mission advances to the next
milestone only after the current one `PASSED` required validation. A `PASSED`
milestone is terminal; corrective work after a *final* validation failure goes
into a **new corrective milestone** (never by re-opening a passed one).

## The control loop

```
while mission is RUNNING/VALIDATING:
    reconcile repo + worker reality
    if active milestone fully terminal:
        run validators (scrutiny, user-testing)
        pass  → milestone PASSED, advance
        fail  → create corrective features (bounded) → re-run
                exhausted → BLOCKED
    else:
        compute dependency-ready, conflict-free batch (≤ max_concurrent)
        dispatch batch (parallel isolated workers)
        reconcile each: commit + merge + conflict detection + classify
    if nothing can run → determine complete / blocked / invalid-graph
    persist (atomic) + append events
```

## Steering & replanning

- **Steer** (`mission_steer`): changes guidance/priority without rebuilding the
  plan (e.g. reprioritize a feature; the scheduler honors it next dispatch).
- **Replan** (`mission_replan`): pauses scheduling, preserves valid completed
  work, marks invalidated work `SUPERSEDED`, adds replacement features, bumps the
  plan revision, and requests re-approval if scope changed materially. History is
  never rewritten (append-only events).

## Event log

Append-only `events.jsonl` (also mirrored in `mission.json`). Event types include
`MISSION_CREATED, REPOSITORY_ANALYZED, PLAN_GENERATED, PLAN_APPROVED,
MILESTONE_STARTED, FEATURE_READY, WORKER_STARTED, WORKER_FINISHED, WORKER_FAILED,
FEATURE_COMPLETED, FEATURE_RETRIED, FIX_FEATURE_CREATED, VALIDATION_STARTED,
VALIDATION_FAILED, VALIDATION_PASSED, MILESTONE_COMPLETED, MISSION_PAUSED,
MISSION_RESUMED, MISSION_STEERED, MISSION_REPLANNED, WORKER_RECONCILED,
MISSION_BLOCKED, MISSION_COMPLETED, MISSION_CANCELLED`. Each event carries
timestamp, mission id, entity id, and a structured payload — enough to fully
reconstruct state for debugging.

## Crash recovery / resume

On `run`/`resume`: load the persisted mission, inspect the repo, reset any
feature left `RUNNING` (no live process) to `READY`, prune orphan worktrees with
no commit, and continue from the last valid state. Because every step persists
atomically before returning, a crash loses at most the in-flight batch, which is
safely re-run.
