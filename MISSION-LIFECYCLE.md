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

## Failure classes

Workers are never trusted on their word — each finished run is classified from
**git reality + raw evidence** (`controller._classify`):

| Class | Evidence | Retryable |
|---|---|---|
| `WORKER_TIMEOUT` | killed at `worker_timeout`, **or** finished within a 10 s wall-clock grace of it | yes |
| `MODEL_LIMIT_FAILURE` (HG-04) | `outputTokenLimitReached: true` / `finish_reason: "length"` in the raw transcript — exit 0, "completed" envelope, but truncated mid-flight | yes (retries with a *"do not re-analyze, implement now"* resume block + the truncated tail) |
| `PROVIDER_FAILURE` | 401/403/429, quota/rate-limit, connection (incl. exit-0 quota answers) | yes |
| `WORKER_CRASH` | non-zero exit, no provider signal | yes |
| `IMPLEMENTATION_FAILURE` | claimed done but no real change, or claimed failed with no test signal | yes |
| `TEST_FAILURE` | test-failure markers | yes |
| `MERGE_CONFLICT` | base-merge conflict (branch preserved, re-run with evidence) | bounded |
| `USER_BLOCKED` | worker reported blocked | no — mission blocks |

Every failure keeps the raw transcript: `workers/<run_id>.raw.json` (full leaf
stdout, 5 MB cap, HG-01) plus the redacted final message `.txt` — classification
bugs can no longer destroy the forensic evidence.

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

**Single source of truth (HG-11):** `events.jsonl` is the canonical append-only
log (redacted on write). The `events` list in `mission.json`/`mission.yaml` is a
**derived** view, hydrated from the jsonl on load and never written back, so the
two can no longer diverge. Reconcile tooling should read `events.jsonl` only.

Event types include `MISSION_CREATED` (stamped with `hamgoose_version`),
`REPOSITORY_ANALYZED`, `PLAN_GENERATED`, `PLAN_FAILED` (with `timed_out`,
`raw_tail`, `attempts`), `PLAN_REVISION_RECORDED`, `PLAN_APPROVED`,
`MILESTONE_STARTED`, `FEATURE_READY`, `WORKER_STARTED`, `WORKER_PROGRESS`
(mid-run bytes/turn hints, HG-14), `WORKER_FINISHED`, `WORKER_FAILED` (with
`duration`, `timed_out`), `FEATURE_COMPLETED` (with `dropped` commit curation,
HG-12), `FEATURE_FAILED`, `FEATURE_RETRIED` (manual retries carry
`beyond_budget`), `FIX_FEATURE_CREATED`, `VALIDATION_STARTED`, `VALIDATION_FAILED`,
`VALIDATION_PASSED`, `MILESTONE_COMPLETED`, `CONFIG_DRIFT` (HG-08),
`MISSION_PAUSED`, `MISSION_RESUMED`, `MISSION_STEERED`, `MISSION_REPLANNED`,
`WORKER_RECONCILED`, `MISSION_BLOCKED`, `MISSION_COMPLETED`, `MISSION_CANCELLED`.
Each event carries timestamp, mission id, entity id, and a structured payload —
enough to fully reconstruct state for debugging.

## Retry budget (HG-09)

The attempt budget counts **automated + manual** retries:
`attempts + manual_retries >= max_attempts` stops a feature being scheduled again.
`mission_retry_feature` increments `manual_retries` and always emits
`FEATURE_RETRIED {manual: true, beyond_budget: …}` so the event stream tells the
truth. Chosen semantics: budget-honoring (option a) with truthful events (b) —
manual retries are not unlimited, and if one is issued at/over the cap the
cap is visible in the event.

## External implementation (HG-13)

Work implemented **outside** the worker pipeline (by the lead agent or a human)
uses `mission_complete_feature` (`controller.complete_feature_external`) instead
of hand-editing state files:

- verifies the commit actually exists in git (claims are never trusted);
- runs the feature's `validation_commands` and records their exit codes;
- runs a **real scrutiny validation** on the diff — `validation[]` is populated,
  closing the old "passed with empty validation" hole;
- appends proper events, curates commits, and if the completion removes the last
  stuck feature it unblocks the milestone/mission and the normal flow continues.

## Plan revisions

Every structural plan change lands in `plan_revisions`: the controller records
revisions for its own changes (initial plan, replan, corrective fix features);
`store.save_mission` diffs the feature/milestone id sets against the previous
persisted state and, if an external writer changed structure without a revision,
records one (`external plan change (store)`) + `PLAN_REVISION_RECORDED`. A
0-feature revision is impossible: `plan()` and `_apply_plan` both refuse it.

## Crash recovery / resume

On `run`/`resume`: load the persisted mission, inspect the repo, reset any
feature left `RUNNING` (no live process) to `READY`, prune orphan worktrees with
no commit, and continue from the last valid state. Because every step persists
atomically before returning, a crash loses at most the in-flight batch, which is
safely re-run.
