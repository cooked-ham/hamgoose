# Hamgoose Mission Issues Report

**Session:** 2026-08-31 (~01:36–03:45 -05:00) · **Repo:** `D:\HamSTACK` · **hamgoose:** 0.1.8 (source at `D:\hamgoose\src\hamgoose`)
**Missions involved:** `M-2026-013808F8` (cancelled, pre-fix), `M-2026-0142179C` (ran, cancelled at MS01 validation)
**Worker/validator/planner model:** `custom_airouter / Qwen3.8` (pinned per user rule) · **Concurrency:** 2 (user rule: >2 concurrent subagents makes the provider drop all connections)
**Companion log:** `DISCREPANCIES.md` (append-only, written live during the session)

## Outcome summary

- **Landed on `main`** (fast-forward `9939feb..c2e32f6`, 19 commits): MS01 of the Meshy-like 4090 plan is implemented and validated — Pixal3D WSL2 provisioner + manifest, Pixal3D worker, server routing/registry, frontend image-to-3D route + GLB preview, per-job immutable artifact tree. **62/62 backend tests pass; frontend production build passes.**
- **Not started:** MS02 (F006–F008: DINOv3 geometry scorer, Hunyuan Shape/TripoSG candidates, best-of-N + benchmark) and MS03 (F009–F011: LATO.2/classical topology + bakes, PBR repaint + judgment, 2K/4K finalization + export). Mission cancelled per user decision; the full plan remains in `docs/meshy_like_4090_appstack.md`.

---

## Issues

### H1 — MCP tool layer silently swallows errors (empty result, no state change, no event) · CRITICAL
**Seen with:** `mission_plan` (explicit features/milestones JSON), `mission_complete_feature`.
**Symptom:** The tool call returned an empty object (`{}` / no `result`) with no error surfaced. No events were appended; the mission state was unchanged — a *silent no-op*. The identical call made directly against the controller (same code path, `MissionController.plan()` / `.complete_feature_external()`) succeeded immediately.
**Evidence:**
- `mission_plan(M-2026-0142179C, features=…11 features…)` → `{}`, zero new events; `mission_status` still `PLANNING`. Direct Python: `ctl.plan(...)` → `AWAITING_APPROVAL`, 11 features / 3 milestones.
- `mission_complete_feature(F001, commit=f19501e…)` → `{}`, no events. Direct Python: `WORKER_FINISHED` + `FEATURE_COMPLETED` recorded.
- Note: `mission_plan` failures *with* a `PLAN_FAILED` event returned `{}` too — so `{}` is also the shape of a swallowed exception, making success and failure indistinguishable to the caller.
**Impact:** Any automation (like this session's goose lead agent) cannot trust tool results; every call needs a post-hoc `mission_events`/`mission_status` verification, and recovery requires bypassing the MCP layer via direct Python (`sys.path.insert(0, r"D:\hamgoose\src"); from hamgoose.server import _controller`).
**Suspected root cause:** the MCP bridge drops the response (large-payload truncation?) or the tool handler catches exceptions and returns nothing instead of propagating the error.
**Recommended fix:**
1. Tool handlers must propagate exceptions as MCP tool errors — never return empty on failure.
2. Every mutating tool should self-verify (re-read state/events) and include the new state in its response.
3. Add a regression test: call each mutating tool with a payload that triggers a `ValueError` and assert the client receives an error, not empty success.

### H2 — Planner (and inherited LLM calls) ignore provider pinning; `planner` config key silently dropped · CRITICAL
**Symptom:** Every LLM planner attempt failed with `Rate limit exceeded: [1308][Usage limit reached for 5 hour. Your limit will reset at 2026-08-31 16:10:39]` — 3 attempts, on two separate missions. The mission config pinned `worker` and `validator` to `custom_airouter/Qwen3.8`, but the planner still used the *inherited default* model on a provider under a 5-hour usage cap.
**Evidence:** `mission_create` accepted `config.planner = {provider, model}` without complaint, but the readiness "EFFECTIVE CONFIG" printout shows only `worker`/`validator`/`execution` — the `planner` key was dropped. The documented config map in the tool description only lists `worker`/`validator` keys.
**Impact:** Mission setup is impossible whenever the inherited model's provider is rate-limited, even though a working pinned model exists. Cost: ~30 minutes of failed plan attempts.
**Recommended fix:**
1. Honor a `planner` role in mission config (or reuse `worker` role) — the planner is an LLM call like any other.
2. Refuse/warn at `mission_create` if a config key is unrecognized instead of silently dropping it.
3. Readiness should show the *effective* provider/model for **planner, worker, and validator** and warn when they differ.

### H3 — Default `worker_timeout=420s` is below the wall time of the pinned worker model · HIGH
**Symptom:** F001 worker attempt 1 was killed at exactly 420.0 s (`WORKER_FAILED timed_out=true`) after already committing its 385-line `setup_pixal3d.sh`; attempt 2 (420 s) produced zero output. F004 attempt 1 ran 446 s only because the timeout had already been raised.
**Root cause:** Readiness flagged the worker model as `Qwen3.8 - SMALL-OUTPUT-BUDGET`, and the default 420 s cap is shorter than that model needs for multi-file features. Nothing connected the two warnings.
**Fix applied (session):** `execution.worker_timeout` 420 → 900 in `mission.json` and in `.goose/hamgoose/config.json`.
**Recommended fix:**
1. Make the timeout model-aware (readiness suggests a value when the model is flagged small-budget/slow), or raise the default.
2. Surface a warning at create time: "worker model flagged SMALL-OUTPUT-BUDGET with 420 s timeout — consider ≥900 s".

### H4 — Validator false-failures: `semantic_timeout=180s` kills the validator mid-run; timeout is indistinguishable from failure · HIGH
**Symptom:** MS01 milestone validation (all 5 features COMPLETED, 62/62 tests passing) produced, for **both** scrutiny and user_testing: `passed=false, severity=major, summary="validator produced no structured verdict", findings=[]`. Every round lasted **exactly 180.0 s** — the validator's `goose run` was killed at the timeout before it could emit the JSON verdict.
**Cascade:** the corrective loop reads `findings` to create fix features — zero findings → zero fixes → the loop just re-ran the same failing validation. All 3 `max_correction_attempts` were consumed in ~9 minutes and the mission went `BLOCKED: milestone MS01 failed required validation after 3 corrections` on a milestone with no defects.
**Evidence:** `validation/MS01-{1,2,3}.json`; `VALIDATION_STARTED 08:27:06 → VALIDATION_FAILED 08:30:06` (180.00 s); raw validator output empty (`RAW LEN 0`).
**Fix applied (session):** `.goose/hamgoose/config.json` with `execution.semantic_timeout=900` (repo-level; see H5 for why mission-level did not work).
**Recommended fix:**
1. Record `timed_out` on `ValidationResult`; a timeout is **not** a quality failure — do not count it toward `max_correction_attempts` (retry once at a longer budget, then surface as BLOCKED-with-reason "validator timeout").
2. Distinguish "no structured verdict + timed_out" from "no structured verdict + completed" in both storage and status rendering.
3. When a validation round produces zero findings, skip creating the no-op corrective cycle.

### H5 — Config precedence inconsistency: validator uses controller-level config; worker uses per-dispatch mission config · HIGH
**Symptom:** Editing the *mission* config (`mission.json`) changed the worker timeout (effective mid-mission) but had **no effect** on the validator timeout. Only the repo-level `.goose/hamgoose/config.json` changed validator behavior.
**Root cause (code):** `MissionController.__init__` builds `SemanticClient(self.config)` where `self.config = Config.load(repo=repo)` — i.e., env + repo file + defaults, **never the mission config**. Worker timeout, by contrast, is read per-dispatch via `self._cfg(m)` which *does* prefer `m.config`. So `semantic_timeout` (validator/planner LLM calls) and `worker_timeout` follow different precedence rules in the same config system.
**Recommended fix:**
1. Build the semantic client (or at least resolve its timeout) from `_cfg(mission)` per call, matching worker behavior.
2. Document one single precedence table (env > mission > repo > defaults) that applies to every role.

### H6 — `max_steps` / attempt-budget / client-timeout interaction looks like a stall · MEDIUM
**Observed:** `mission_run(max_steps=2)` executed two *dispatches* (each a full blocking worker run); auto-retries inside a dispatch consume the feature's attempt budget but not a step. After the budget, the loop exits and a queued retry (e.g., F004 attempt 2) waits for the next `mission_run` — with no worker process and no events, the system looks dead for minutes. Additionally the lead agent's 300 s client sandbox times out while the server loop keeps running (good), but the tool returns nothing to the caller (see H1), so progress is only visible by polling `mission_events`.
**Recommended fix:**
1. In the `mission_run` response, state explicitly: "N dispatches completed; M features queued/ready; call again to continue".
2. Document that `max_steps` = dispatches, and that FEATURE_RETRIED is not a step.
3. Consider a `mission_watch`/progress-pull tool or longer-lived progress channel so the caller never has to guess whether the loop is alive.

### H7 — Qwen3.8 repeatedly emits no completion envelope: work committed, run classified `IMPLEMENTATION_FAILURE` · HIGH (model behavior + classifier)
**Symptom:** F002, F004, F005 all failed **attempt 1** with `IMPLEMENTATION_FAILURE` after 6–11 minutes, even though the work was committed to the worktree (e.g., F002's 409-line `pixal3d.py` existed in `be7bf4f` before the "failure"). The retry — with prior failure evidence in the prompt — succeeded, sometimes in under 2 minutes.
**Root cause (two-part):**
1. Model: Qwen3.8 (small output budget) finishes the code but often ends the run without the required final JSON envelope (or with a malformed one).
2. Classifier: `_classify` maps "claimed ok but no real change" / "unparseable output" to `IMPLEMENTATION_FAILURE` without first checking that the worktree diff proves the work exists. An envelope failure is treated like a code failure, burning an attempt and 6–11 minutes.
**Recommended fix:**
1. Before classifying `IMPLEMENTATION_FAILURE`, check `git diff --stat` vs the base: if substantial changes exist, classify a distinct `ENVELOPE_FAILURE` (retryable, cheaper, prompt emphasizes "you already wrote the code — emit the result JSON").
2. Strengthen the worker prompt's final-envelope contract (single fenced JSON, no prose after).
3. Readiness should warn that SMALL-OUTPUT-BUDGET models are prone to envelope failures.

### H8 — Event-stream read lag (~5 min) caused a misdiagnosis of a dead loop · LOW
**Symptom:** `WORKER_FAILED`/`FEATURE_RETRIED` at 07:13:14 were not visible via `mission_events` until ~07:18, while the worker process was already reaped. From one stale poll it looked like the control loop had died and the worker was orphaned (it had not).
**Recommended fix:** `events.jsonl` appends should be immediately visible to concurrent readers (check buffering/flush on the append path); status rendering could include "last event age" so callers can tell lag from death.

### H9 — Worker scratch files committed into the mission history · LOW
**Symptom:** F005 attempts left `_f005_apply.py`, `_f005_repro*.py`, `_f005_*.diff`, `_f005_tb.txt` (a 730-line traceback), etc. in the repo root; two of them were committed to the mission branch (the final successful attempt cleaned the working tree, but the history keeps the files).
**Recommended fix:** worker prompt: "never create scratch/debug files inside the repository; use /tmp". Optionally a pre-merge cleanup of `_*` root files.

### H10 — Readiness warnings exist but have no action hook · LOW
**Symptom:** Readiness printed `Worker model: Qwen3.8 - SMALL-OUTPUT-BUDGET` and `Dirty working tree` — both of which predicted H3/H4/H7 — but there is no mechanism (suggestion, config auto-adjust, or gate) that acts on them; they are printed and forgotten.
**Recommended fix:** readiness should emit concrete suggested config deltas (e.g., `worker_timeout≥900`) that can be applied with one call, and offer to auto-apply.

### H11 — Stale missions accumulate in the repo · LOW (housekeeping)
**Symptom:** At session start, `mission_list` showed 4 live-ish missions (1 AWAITING_APPROVAL, 3 PLANNING, 1 COMPLETED) from earlier sessions, several with identical overlapping goals. Nothing prunes them; they clutter `mission_list` and risk accidental crosstalk.
**Recommended fix:** a `mission_gc`/archive for terminal and long-stale missions; surface stale missions in `mission_list` output.

---

## What worked (for balance)

- **Worktree isolation + auto-commit:** every worker attempt left recoverable commits; attempt 1 of F001 timed out *after* committing its main deliverable, which made external completion trivial.
- **`_reconcile`:** stale RUNNING→READY recovery and orphan-worktree pruning behaved correctly.
- **External completion path (`complete_feature_external`)** did exactly what its docstring claims (commit verification, validation commands, real scrutiny, proper events, legal state transitions) — it was the reliable path twice.
- **Fast-forward merge** of `mission/base` → `main` was clean; mission cancel cleaned the worktrees.
- **Repo-level `.goose/hamgoose/config.json` (HG-08)** is a genuinely useful configuration surface — it is the only place that fixed the validator (H4/H5).

## Reproduction / verification commands used this session

```bash
python -m pytest backend/tests -q          # 62 passed
cd app && npm run build                     # ✓ built
git rev-list --count main..mission/base     # 19 commits (now merged, ff)
```

## Recommended sequencing to fix

1. **H1** (silent tool failures) — without this, nothing else is operable by automation.
2. **H2 + H5** (provider pinning + config precedence) — one config-system fix covers both.
3. **H4** (timeout ≠ failure in validation) — unblocks milestone gates for slow models.
4. **H7** (envelope-failure classification) — cuts wasted worker attempts ~50%.
5. **H3/H6/H8–H11** — quality-of-life.
