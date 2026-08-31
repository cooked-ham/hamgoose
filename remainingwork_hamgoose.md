# Remaining Work — hamgoose (Goose CLI mission extension)

Repo: **`D:\hamgoose`** · Baseline: `a1b1476` (v0.1.7 — "fix leaf worker env leak, live progress
notifications, timeout hardening"; prior: v0.1.6 "empty-plan hardening") · Date: 2026-08-31
Companion: `D:\HamSTACK\docs\HAMGOOSE-WORKLIST.md` (evidence for every item) and
`D:\HamSTACK\docs\remainingwork_hamstack.md` (the product repo's own backlog).

**Why this file exists:** five consecutive missions against HamSTACK (M-2026-1909541C →
M-2026-221249D6) exposed that the *extension* — not the target repo — is the bottleneck:
4 of 5 missions never produced a plan, and all 12 worker dispatches on the 5th died in the
exploration phase (model output-token limit + thin planner context + 180 s planner kill).
Fixing these items is what makes future missions actually dispatchable work.

Conventions: items **HG-01…**, priority **P0** (kills worker/planner runs) > **P1** (state/config
integrity) > **P2** (observability) > **P3** (hygiene). Effort **S** < 1 h, **M** 1–4 h, **L** > 4 h.

---

## Phase 0 — Quick wins (do first, unblock everything else)

### HG-01 · Persist full worker transcripts — P0 · S
- **Problem:** 11 of 12 worker transcripts in `M-2026-221249D6/workers/` contain only the final
  assistant message; the one full `goose run` JSON (`W-AA657F7A.txt`) is what made
  `outputTokenLimitReached: true` forensics possible at all.
- **Fix:** in `worker.py::GooseRunBackend.run`, before `extract_text`, write the raw stdout
  (and stderr tail) to `<mission_dir>/workers/<run_id>.raw.json` (best-effort, size-capped
  e.g. 5 MB). Keep the redacted final message as today's `.txt`.
- **Acceptance:** a scripted MockBackend/real-leaf run leaves `.raw.json`; a test asserts the
  file exists and parses as JSON when output is JSON.

### HG-02 · Repo hygiene in `D:\hamgoose` — P3 · S
- Delete stray 0-byte file `(3` at repo root (check tracked-vs-untracked first — it appeared in
  HamSTACK too; same origin pattern).
- Inspect/remove `hg-timing-3j7f6kbd/` (leftover temp dir) and decide disposition of `_e2e/`
  (contains its own `.goose`; keep or archive).
- Keep `e2e.txt` (`e2e-ok`) — it is an intentional, committed e2e artifact.
- **Acceptance:** `git status` clean; no zero-byte or temp dirs at root.

### HG-03 · Windows gotchas doc — P3 · S
- Add a section to `README.md` (or a `WINDOWS.md`): cmd multi-line `python -c` truncation,
  cp1252 stdout (`sys.stdout.reconfigure(encoding="utf-8")`), CRLF sources
  (normalize→edit→restore), `taskkill /F /T` tree kills, and the `goose run` grandchild
  pipe-hang rationale (promote the existing `gosub.py` module docstring).
- **Acceptance:** section present; links from README + TESTING.md.

---

## Phase 1 — P0: stop the worker & planner death spiral

### HG-04 · Classify model output-token-limit deaths + resume-on-retry — P0 · M
- **Problem:** a truncated leaf run exits 0 with envelope `status: "completed"` but no result
  JSON → indistinguishable `IMPLEMENTATION_FAILURE`. The 420.8 s run (F002 attempt 3) missed
  `WORKER_TIMEOUT` by 0.8 s of wall time.
- **Fix:**
  1. `models.py`: add `FailureClass.MODEL_LIMIT_FAILURE` to the enum (line ~54) and to
     `RETRYABLE_FAILURES` (line ~69).
  2. `controller.py::_classify` (~line 744): detect `"outputTokenLimitReached": true` (or
     `finish_reason == "length"` markers) in `res.raw` → return the new class.
  3. Boundary fix: in the failure path (~line 709), classify as timeout when
     `res.timed_out or (duration ≥ worker_timeout − 10 s grace)` — record `duration` in the
     worker block (start/end already stored).
  4. `prompting.py`: when retrying a `MODEL_LIMIT_FAILURE`, prepend the truncated final message
     with the instruction: *"Your previous run was cut off mid-analysis at the model's output
     limit. Do not re-analyze. Implement the feature now, minimally, then report."*
- **Acceptance:** new tests in `tests/test_failure_classification.py`
  (scripted `WorkerResult` fixtures: limit-death → new class + retryable; 420.8 s fixture →
  timeout class; clean-done-with-changes → accepted).

### HG-05 · Code-first worker prompt — P0 · M
- **Problem:** workers burned 100 turns (~400 s) reading and analyzing, never writing. Every
  one of 12 transcripts ends mid-exploration; `W-1C812C93` finished a *correct* 4-point
  diagnosis and then got cut off before editing.
- **Fix:** rewrite `prompting.py::worker_prompt` (322-line module):
  - hard budget lines: "Analysis ≤ 5 bullets, one screen max" /
    "Write the first file change **before** any second analysis pass" /
    "A minimal working change with a real test beats a complete analysis" /
    "If you cannot verify locally, implement the minimal version and say so in `notes`".
  - keep the result-JSON contract block verbatim (parsing depends on it).
- **Acceptance:** `tests/test_plan.py`-style unit test asserting the new constraints are present
  in the rendered prompt; then a live smoke mission on a scratch repo where a worker (any
  model) must produce a commit in ≤ 1 attempt — record attempt count in `PROGRESS`-style notes.

### HG-06 · Planner observability + separate planner timeout — P0 · M
- **Problem:** planner = `semantic.complete()` → leaf `goose run` killed after the shared
  `semantic_timeout` (factory **180 s**), raw output discarded; missions 3 & 4 died with **zero
  PLAN events**; the only evidence of the "300 s planner timeouts" from prior sessions is
  unrecoverable.
- **Fix:**
  1. `config.py::ExecutionConfig`: add `planner_timeout: int = Field(default=600, ge=1)`
     (keep `semantic_timeout` for validator/diagnosis calls).
  2. `semantic.py`: `SemanticClient.complete(prompt, role, timeout=None)` — honor an explicit
     timeout, pass it through `_goose_run` → `gosub.run_captured`; return a result object
     `(text, timed_out, raw_tail)` instead of a bare string (update all call sites).
  3. `controller.py::plan()`/`_generate_plan` (~lines 199–250): on empty plan, the
     `PLAN_FAILED` payload must carry `{"timed_out": bool, "raw_tail": <redacted 2 KB>,
     "attempts": n}`; on timeout, retry **once with a smaller repo-analysis slice** (e.g.
     top-level tree + README head only) before failing.
- **Acceptance:** `tests/test_semantic.py` + `tests/test_plan.py`: scripted timeout → event
  payload contains `timed_out: true` + raw tail; small-slice retry path exercised; no silent
  death possible (every planner exit produces an event).

### HG-07 · Model-capability preflight at `missionCreate` — P0 · L
- **Problem:** `inherit` silently resolved to glm-5.3-flash (small per-message output budget)
  for workers; nothing warned. 12 doomed dispatches ≈ 38 min.
- **Fix:**
  1. In create/readiness (`register.py`/`server.py` readiness path + `plan.py::check_readiness`
     style): run one bounded smoke leaf (≤ 60 s, `--max-turns 2`) with the resolved worker
     model: a ~4 KB prompt requiring a fenced JSON result.
  2. Record in `mission.repo_analysis.model_check`: `{model, ok, output_tokens,
     limit_evidence, duration}` + readiness line
     `"Worker model: <name> — smoke OK/SMALL-OUTPUT-BUDGET/WARN"`.
  3. Surface the line in `missionStatus` output so the lead/user sees it before approving.
- **Acceptance:** readiness report shows the model line; a mocked smoke-failure flips the line
  to WARN and is visible in status; unit test with scripted smoke output.

---

## Phase 2 — P1: config & state integrity

### HG-08 · Canonical config channel — P1 · M
- **Problem:** hand-editing a mission's `mission.json/yaml` does not affect other missions
  (`Config.load` reads `HAMGOOSE_CONFIG` env + `missionCreate` overrides only). Evidence:
  turns→100 landed on all 5 missions; the 1800 s timeout bump landed on 1; the final mission
  ran factory 420 s unnoticed.
- **Fix:**
  1. `config.py::Config.load`: read an optional per-repo default file
     `<repo>/.goose/hamgoose/config.json` (JSON, same schema) between env and overrides;
     document in `CONFIGURATION.md`.
  2. `missionCreate` output: echo the **effective** config (resolved values, not "inherit").
  3. `store.py::save_mission`: warn (event `CONFIG_DRIFT`) when the persisted `config`
     execution block differs from what the controller is actually using.
- **Acceptance:** tests: file read precedence (env < file < overrides), drift event fires on
  hand-edited file; `CONFIGURATION.md` section added.

### HG-09 · Manual-retry attempt budget — P1 · S/M
- **Problem:** `retry_feature` (controller ~line 984) sets status `READY` without touching
  `attempts`; `scheduler.ready_features` only excludes `FAILED`/`NEEDS_FIX` when exhausted —
  so manual retries bypass `max_feature_attempts` (observed: attempts 6, cap 3).
- **Fix (choose one, document in MISSION-LIFECYCLE.md):**
  - (a) budget-honoring: track `f.manual_retries`; exhaustion = `attempts + manual_retries ≥
    max_attempts` in both `ready_features` and the failure path; or
  - (b) explicit-bypass: keep unlimited manual retries but emit
    `FEATURE_RETRIED {manual: true, beyond_budget: true}` so events tell the truth.
- **Acceptance:** `tests/test_scheduler.py` + lifecycle test: automated retries stop at cap
  either way; event payload matches the chosen semantics.

### HG-10 · Plan-revision bookkeeping + anomaly investigation — P1 · M
- **Problem A:** direct store writes don't record `plan_revisions` (final mission:
  `plan_revisions: []` despite `current_revision: 1`).
- **Problem B:** mission `M-2026-1909541C` persisted a **0-feature "initial plan"** revision
  *and* reached `AWAITING_APPROVAL` — impossible under current `controller.plan()`.
- **Fix:**
  1. `store.py::save_mission`: diff features/milestones against the last saved state; on
     structural change without a corresponding revision, append one
     (`note: "external plan change (store)"`).
  2. Forensics (research task): read `M-2026-1909541C/mission.yaml` event order +
     `git log` of hamgoose at 2026-08-31 00:09–00:15 (commits `50468e1`/`191e2ae`/`a1b1476`
     era) to identify the writer of the empty revision; add a regression test asserting
     `plan()` can never persist a 0-feature revision.
- **Acceptance:** both problems closed; regression tests green.

### HG-11 · Single source of truth for events — P1 · M
- **Problem:** `append_event`/`save_mission` keep **both** `events.jsonl` and the yaml/json
  `events:` list — two stores that can diverge; reconcile scripts must sync both.
- **Fix:** keep `events.jsonl` canonical (append-only, redacted-on-write); make the yaml
  `events:` list a **derived** field: stop writing it in `save_mission`, hydrate it from the
  jsonl on `load_mission` (keep the field for compatibility; document in MISSION-LIFECYCLE.md).
- **Acceptance:** round-trip test: mutate jsonl after save → reload reflects it; no dual-write
  code path remains; `tests/test_store.py` extended.

### HG-12 · Curate `feature.commits` — P1 · S
- **Problem:** append-only commits list recorded junk commit `ba91670` on F001 permanently.
- **Fix:** `store.py`: add `prune_commits(mission, keep: set[str])`; call it in
  `controller` on `FEATURE_COMPLETED` (keep = commits reachable from the feature's
  `workdir`/branch tip via `git.py`), never dropping commits already in `mission.head_commit`
  history silently — record dropped hashes in the `FEATURE_COMPLETED` payload
  (`dropped: [...]`).
- **Acceptance:** test: junk hash pruned, payload lists it.

### HG-13 · First-class external-implementation path — P1 · L
- **Problem:** lead-agent-implemented work required a hand-rolled `_reconcile.py` (raw status
  writes, manual events, validators declared "passed" with `validation: []`).
- **Fix:** `controller.py`: add
  `complete_feature_external(mission_id, feature_id, summary, changed_files, tests, commit)`:
  verify commit exists (`git.py`), run `validation_commands` if present, run
  `missionValidate("scrutiny")` on the diff (wire the real validator — closes the
  "passed with empty validation" hole), append proper events, curate commits (HG-12), then
  continue the normal milestone flow.
- **Acceptance:** integration scenario in `tests/integration/test_lifecycle.py`: mock worker
  fails → external completion with a real scratch-repo commit → feature COMPLETED with a
  populated `validation[]`; milestone advances.

---

## Phase 3 — P2: observability

### HG-14 · Mid-run worker progress events — P2 · L
- **Problem:** 420 s of silence per dispatch; the pipeline looked hung while workers were
  alive (and vice versa).
- **Fix:** `gosub.run_captured` already writes temp stdout files — add an optional watcher
  (thread, 5 s poll) in `GooseRunBackend.run` that emits a mission event
  `WORKER_PROGRESS {feature, run_id, turn_hint, bytes, elapsed}` (best-effort, deduped when
  unchanged) and stops on completion. Keep it off the MCP call path (mission run stays
  bounded by `max_steps_per_run`).
- **Acceptance:** lifecycle test with a slow MockBackend emits ≥ 1 `WORKER_PROGRESS`; event
  stops after terminal state.

### HG-15 · Repo-analysis quality fixes — P2 · M
- **Problem:** constant 1 826-char summary across all 5 missions; README `instructions`
  truncated **mid-word**; `is_repo: false` in 4 of 5 missions on an obvious git repo.
- **Fix:**
  1. Find the truncation cap (search `controller.py` create/repo-analysis path +
     `prompting.decompose_prompt` inputs); replace fixed-char cut with a structured digest:
     top-level tree (depth 2, line-capped), README head (paragraph-boundary cut, ≤ 8 KB),
     PLAN.md/PROGRESS.md heads when present.
  2. `git.py::is_repo`: debug the false negatives (cwd vs repo path handling — the analysis
     ran from a different directory); add unit tests for both layouts.
- **Acceptance:** on HamSTACK: summary varies with repo content, README ends at a paragraph
  boundary, `is_repo: true`; unit tests green.

---

## Test strategy (applies to all phases)

- Harness already exists: `tests/harness.py` + `MockBackend` (`worker.py`) + scripted
  semantic responses — every controller fix above gets a MockBackend scenario, no live model
  needed.
- New files: `tests/test_failure_classification.py` (HG-04), `tests/test_retry_budget.py`
  (HG-09), `tests/test_plan_revisions.py` (HG-10), `tests/test_config_channel.py` (HG-08),
  `tests/test_external_completion.py` (HG-13); extend `test_semantic.py` (HG-06),
  `test_store.py` (HG-11/12), `test_scheduler.py` (HG-09), `integration/test_lifecycle.py`
  (HG-05/13/14).
- Keep `tests/test_speed.py` gates (pipeline speed is a v0.1.7 feature — regressions fail CI).
- Live validation (manual, once per phase): a scratch git repo + real `goose run` leaf via
  `tests/integration/test_real_goose.py` patterns.

## Definition of done (whole file)

1. `pytest` green in `D:\hamgoose` (existing + new tests).
2. A **re-run of the HamSTACK mission goal** (or a smaller clone of it) on a scratch repo with
   the same inherited model completes ≥ 1 feature via a dispatched worker without manual
   reconcile — this is the end-to-end proof.
3. Every failure mode observed in M-2026-221249D6 now emits a distinguishable event with raw
   evidence attached.
4. `README.md` / `CONFIGURATION.md` / `MISSION-LIFECYCLE.md` updated to match.

## Out of scope (recorded, not assigned)

- Provider/model choice (user constraint: max 3 connections incl. lead; `inherit` stays the
  default — preflight only *reports*, never switches models).
- `server.py` transport / npm launcher internals (v0.1.7 env-leak fix stands).
- The original build mission (`coding_plan.md`, 1 720 lines) is historical; reference only.
