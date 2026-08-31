# hamgoose Hardening — Fix Plan & Implementation Record

Companion to `remainingwork_hamgoose.md` (the failure evidence). This document is the
fix plan for every recorded fail point, plus the hardening practices adopted so a
controlled harness pipeline cannot fail silently again.

Baseline when this work started: commit `10425de`, pytest **75 passed** (requires the
Windows basetemp workaround — see HG-17). Target: all HG-01…HG-17 closed, new tests green.

---

## 0. Root-cause summary (what actually killed the 5 missions)

| # | Root cause | Evidence | Closed by |
|---|------------|----------|-----------|
| 1 | Workers exhausted the model output-token limit mid-exploration; exit 0 + `status:"completed"` envelope made it indistinguishable from `IMPLEMENTATION_FAILURE` | `W-AA657F7A.txt` — `outputTokenLimitReached: true`; 420.8 s run missed `WORKER_TIMEOUT` by 0.8 s | HG-01, HG-04 |
| 2 | Worker prompts had no exploration budget → 100 turns of analysis, zero file writes | all 12 transcripts end mid-exploration; `W-1C812C93` had a correct diagnosis then died before editing | HG-05 |
| 3 | Planner shared the 180 s `semantic_timeout`; output discarded on kill; every planner exit must produce an event | missions 3 & 4: zero PLAN events | HG-06 |
| 4 | `inherit` silently resolved to a small-output-budget model; no preflight | 12 doomed dispatches ≈ 38 min | HG-07 |
| 5 | Hand-edited mission config only affected one mission (env+overrides channel only) | turns→100 landed on all 5, 1800 s timeout on 1, factory 420 s unnoticed on the last | HG-08 |
| 6 | **Version skew** (found during forensics, new): the mission that persisted a 0-feature plan and reached `AWAITING_APPROVAL` was driven by a **stale installed hamgoose** predating v0.1.6 empty-plan hardening — `__init__.py` (0.1.6), `pyproject.toml` (0.1.7) and `npm/package.json` (0.1.3) disagreed, so nothing flagged the mismatch | `M-2026-1909541C/events.jsonl`: `MISSION_CREATED` 00:09:54 → `REPOSITORY_ANALYZED` 00:14:54 → `PLAN_GENERATED {features:0}` 00:15:50 (the pre-hardening code path); working tree already contained the v0.1.6 guard | HG-16 |

---

## 1. Work items

### Phase 0 — quick wins

**HG-01 · Persist full worker transcripts — P0**
- `WorkerResult` gains `raw_stdout` (captured, size-capped 5 MB). `GooseRunBackend.run`
  stores the raw `goose run` stdout (+ stderr tail) on the result.
- `controller._reconcile_result` writes `<mission>/workers/<run_id>.raw.json`
  (best-effort) before/alongside today's redacted final-message `.txt`.
- Tests: `tests/test_worker_transcripts.py` — scripted run leaves `.raw.json` that
  parses as JSON when output is JSON; `.txt` unchanged behavior.

**HG-02 · Repo hygiene — P3**
- `(3` already removed (commit `10425de`); verified absent on disk.
- Delete `hg-timing-3j7f6kbd/` (leftover temp dir) and stale `.tmp_*` scratch dirs.
- `_e2e/` deleted after inspection (local e2e workspace; `e2e.txt` (`e2e-ok`) stays —
  it is the intentional committed artifact).
- Acceptance: `git status` clean; no zero-byte or temp dirs at root.

**HG-03 · Windows gotchas doc — P3**
- New `WINDOWS.md` (cmd one-line truncation, cp1252 stdout reconfigure, CRLF
  normalize→edit→restore, `taskkill /F /T` tree kills, grandchild pipe-hang rationale,
  pytest temp-dir permission workaround) linked from README + TESTING.md.
- Permanent suite fix in `pyproject.toml` (`--basetemp`, cache dir) → HG-17.

**HG-17 · (new) Deterministic test runs on Windows — P2**
- Baseline run errored 35× with `PermissionError: …\Temp\pytest-of-Sean`; `--basetemp`
  fixes it. Bake into `[tool.pytest.ini_options] addopts` so CI/local are identical.

### Phase 1 — stop the worker & planner death spiral (P0)

**HG-04 · Classify model output-token-limit deaths + resume-on-retry**
- `models.py`: `FailureClass.MODEL_LIMIT_FAILURE` + added to `RETRYABLE_FAILURES`.
- `controller._classify`: detects `outputTokenLimitReached`, `finish_reason: "length"`,
  `"output token limit"` markers in raw evidence → `MODEL_LIMIT_FAILURE`.
- Boundary fix: `WORKER_TIMEOUT` when `res.timed_out OR duration ≥ worker_timeout − 10 s`
  grace; duration recorded as `WorkerRecord.duration_s` (start/end already stored).
- `prompting.worker_prompt`: `MODEL_LIMIT_FAILURE` retry prepends the truncated final
  message + the *"do not re-analyze, implement now"* instruction.
- Tests: `tests/test_failure_classification.py` (limit-death → new class + retryable;
  420.8 s fixture → timeout; clean-done-with-changes → accepted).

**HG-05 · Code-first worker prompt**
- `worker_prompt` gains hard budget lines: analysis ≤ 5 bullets / one screen, first file
  change before any second analysis pass, minimal working change + real test beats
  complete analysis, un-verifiable → implement minimal + say so in `notes`.
- The `OUTPUT CONTRACT` block stays byte-identical (parser depends on it).
- Tests: prompt-constraint unit test (`tests/test_prompting.py`) + integration scenario.

**HG-06 · Planner observability + separate planner timeout**
- `config.ExecutionConfig.planner_timeout: int = 600` (`semantic_timeout` remains for
  validator/diagnosis).
- `semantic.SemanticClient.complete_detailed(prompt, role, timeout, max_turns)` →
  `SemanticResult{text, timed_out, raw_tail, exit_code, duration}`; `complete()` kept as
  a compatibility wrapper.
- `controller.plan()`/`_generate_plan`: every planner exit emits an event; empty plan →
  `PLAN_FAILED {timed_out, raw_tail (redacted 2 KB), attempts}`; on timeout/empty, one
  retry with a smaller repo-analysis slice (top-level tree + README head) before failing.
- Tests: `test_semantic.py` + `test_plan_observability.py` (scripted timeout → payload
  with `timed_out: true` + raw tail; small-slice retry path; no silent planner death).

**HG-07 · Model-capability preflight at missionCreate**
- Bounded smoke leaf (≤ 60 s, `--max-turns 2`) with the **resolved** worker model: ~4 KB
  prompt requiring a fenced JSON reply.
- Records `mission.repo_analysis.model_check = {model, ok, output_tokens, limit_evidence,
  duration, verdict}`; readiness line `Worker model: <name> — smoke OK / SMALL-OUTPUT-
  BUDGET / WARN`; surfaced in `missionStatus`.
- Preflight only *reports* — it never switches models (user constraint).
- Tests: `tests/test_preflight.py` (scripted smoke output; failure flips to WARN).

**HG-16 · (new) Version-skew detection — P0**
- Single source of truth: `hamgoose.__version__` derived from installed package metadata
  (fallback: pyproject literal); npm `package.json` version synced.
- `MISSION_CREATED` payload, readiness report, and `missionStatus` all stamp
  `hamgoose_version` — a stale installed copy is now visible on the first tool call.
- Tests: version stamp present in created mission + readiness render.

### Phase 2 — config & state integrity (P1)

**HG-08 · Canonical config channel**
- `Config.load(overrides, repo=None)` reads `<repo>/.goose/hamgoose/config.json`
  (same schema) **between** env and per-mission overrides: env < repo file < overrides.
- `missionCreate` echoes the **effective** config (resolved values, not "inherit").
- `store.save_mission` emits `CONFIG_DRIFT` when the on-disk execution block differs
  from the one being persisted (hand-edits become visible instead of silently lost).
- Tests: `tests/test_config_channel.py` (precedence, drift event) + `CONFIGURATION.md`.

**HG-09 · Manual-retry attempt budget**
- Chosen semantics: **budget-honoring** (option a) with truthful events (option b):
  `Feature.manual_retries` tracked; exhaustion = `attempts + manual_retries ≥ max_attempts`
  in both `scheduler.ready_features` and the controller failure path;
  `FEATURE_RETRIED {manual: true, beyond_budget: …}` always emitted on manual retry.
- Tests: `tests/test_retry_budget.py` + `test_scheduler.py` extension; documented in
  `MISSION-LIFECYCLE.md`.

**HG-10 · Plan-revision bookkeeping + anomaly investigation**
- `store.save_mission` diffs feature/milestone id sets vs the previous on-disk state; a
  structural change with no covering revision appends one (`external plan change (store)`).
- Controller now appends a revision whenever *it* mutates structure mid-flight
  (corrective fix features), so store-level appends mean "external writer".
- **Forensics closed**: the 0-feature revision + `AWAITING_APPROVAL` in
  `M-2026-1909541C` was written by a stale pre-v0.1.6 install (see §0 row 6 / HG-16).
- Regression tests: `tests/test_plan_revisions.py` — `plan()` can never persist a
  0-feature revision; external structural change produces a revision.

**HG-11 · Single source of truth for events**
- `events.jsonl` is canonical (append-only, redacted-on-write). `save_mission` no longer
  writes the `events:` list into mission.json/yaml; `load_mission` hydrates
  `mission.events` from the jsonl. Field kept for compatibility.
- Tests: `test_store.py` extension — mutate jsonl after save → reload reflects it; no
  dual-write path remains.

**HG-12 · Curate `feature.commits`**
- `store.prune_commits(mission, feature_id, keep)`; controller prunes on
  `FEATURE_COMPLETED` (keep = commits reachable from the feature branch tip via
  `git rev-list base..branch`); dropped hashes recorded in the event payload.
- Tests: `tests/test_commit_curation.py` — junk hash pruned and listed in payload.

**HG-13 · First-class external-implementation path**
- `controller.complete_feature_external(mission_id, feature_id, summary, changed_files,
  tests, commit)`: verifies the commit exists, runs `validation_commands`, runs a real
  scrutiny validation on the diff (populated `validation[]` — closes the
  "passed with empty validation" hole), appends proper events, curates commits, then
  continues the normal milestone flow.
- Exposed as MCP tool `mission_complete_feature` for lead-agent-implemented work.
- Tests: `tests/test_external_completion.py` + integration scenario (mock worker fails →
  external completion → COMPLETED with populated validation → milestone advances).

### Phase 3 — observability (P2)

**HG-14 · Mid-run worker progress events**
- `gosub.run_captured(..., on_poll=None)`: watcher loop polls the temp output files every
  5 s while the leaf runs; `GooseRunBackend.run(..., on_progress=cb)` parses bytes +
  turn hints and forwards; controller emits `WORKER_PROGRESS {feature, run_id, turn_hint,
  bytes, elapsed}` (best-effort, deduped when unchanged, stops at process exit).
- Off the MCP call path — a `mission_run` call stays bounded by `max_steps_per_run`.
- Tests: slow mock backend lifecycle emits ≥ 1 `WORKER_PROGRESS`; no events after
  terminal state.

**HG-15 · Repo-analysis quality**
- Fixed-char cuts replaced by a structured digest: top-level tree (depth 2, entry/line
  capped, junk dirs skipped), README head cut at a **paragraph boundary** (≤ 8 KB),
  PLAN.md/PROGRESS.md heads when present; instructions join cut at paragraph boundary.
- `git.py` hardened: every invocation uses `git -C <abs-path>` (kills the cwd-vs-repo
  false negatives); unit tests for plain-repo and worktree layouts.
- Tests: `tests/test_repo_analysis.py` — summary varies with repo content, README ends at
  a paragraph boundary, `is_repo: true` on both layouts.

---

## 2. Hardening practices adopted (beyond the HG items)

1. **Bounded budgets at every layer** — planner (600 s), workers (per-feature timeout +
   10 s grace classification), validators (`semantic_timeout`), MCP calls
   (`max_steps_per_run`), preflight smoke (60 s / 2 turns). Nothing runs unbounded.
2. **No silent death** — every planner/worker/preflight exit path emits a distinguishable
   event with raw evidence attached (`PLAN_FAILED`, `WORKER_PROGRESS`, `WORKER_FAILED`,
   `FEATURE_RETRIED`, `CONFIG_DRIFT`, model check). Indistinguishable failure classes
   were the #1 forensics blocker.
3. **Evidence-first forensics** — raw transcripts (`.raw.json`) are persisted size-capped
   *before* any parsing, so classification bugs never destroy the evidence.
4. **Single sources of truth** — events (jsonl), config (env < repo file < overrides),
   version (package metadata). Derived mirrors are never written.
5. **Never trust self-reports** — workers' `status:"completed"` is cross-checked against
   git reality; external completions run the same scrutiny validator.
6. **Version stamps on everything** — mission creation records the extension version;
   skew is visible on the first tool call (HG-16).
7. **Deterministic replay** — every failure mode has a MockBackend/scripted scenario in
   the suite; live `goose` validation stays a thin, separate layer
   (`tests/integration/test_real_goose.py`).
8. **Speed is a feature** — `test_speed.py` gates stay; parallel validation stays.

---

## 3. Definition of done

1. `pytest` green in `D:\hamgoose` (existing + new tests) on Windows without manual flags.
2. Scratch-repo re-run of a HamSTACK-sized goal completes ≥ 1 feature via a dispatched
   worker without manual reconcile (manual/live validation step).
3. Every failure mode observed in `M-2026-221249D6` emits a distinguishable event with
   raw evidence attached.
4. `README.md` / `CONFIGURATION.md` / `MISSION-LIFECYCLE.md` / `WINDOWS.md` /
   `TESTING.md` updated to match.

## 4. Status

**Implemented & verified in this pass — all of HG-01…HG-17.**

Verification (Windows, flag-free `pytest` via the baked-in `--basetemp`):

| Suite | Result |
|---|---|
| Deterministic (`-m "not realgoose"`) | **128 passed, 0 failed** (was 59 before this pass) |
| Live Goose (`-m "realgoose"`) | **2 passed, 2 skipped** (skips = provider quota exhausted, detected from raw transcripts — environment, not a pipeline bug) |

Repo hygiene (HG-02): zero-byte files and stray temp/scratch dirs removed; `git status`
clean apart from the intended change set. Docs updated (README, CONFIGURATION,
MISSION-LIFECYCLE, TESTING, WINDOWS).

Remaining (manual, by design): a live end-to-end re-run of a HamSTACK-sized goal on a
scratch repo once provider credits are restored — that is the final proof in §3.2, and
it cannot be automated while the LLM quota is exhausted.

