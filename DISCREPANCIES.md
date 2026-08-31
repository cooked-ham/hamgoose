# Mission Discrepancies & Failure Log

Append-only log of failures, rate limits, and discrepancies observed during hamgoose mission runs in this repo. Newest entries first.

Session scope: hamgoose missions `M-2026-013808F8` (cancelled at planning) and `M-2026-0142179C` (planned, partially run, cancelled by user decision after MS01 validation false-block) for the 4090 Meshy-like image-to-3D stack (`docs/meshy_like_4090_appstack.md`). hamgoose version: 0.1.8. All LLM usage intended via `custom_airouter` / `Qwen3.8`. Max 2 concurrent subagents per provider constraint.

Companion document: `HAMGOOSE_ISSUES_REPORT.md` (H1–H11, severity-rated, for upstream hamgoose fixes).

---

## Fix pass — 2026-08-31 (hamgoose 0.2.0)

All eleven issues from `HAMGOOSE_ISSUES_REPORT.md` (H1–H11) are fixed in this
repo. Summary of the code changes, mapped to the entries above:

### H1 — MCP tool layer no longer swallows errors
- Every mutating tool (`mission_create/plan/approve/run/pause/resume/steer/
  replan/cancel/retry_feature/complete_feature/validate`) catches exceptions and
  returns `TOOL_ERROR: <type>: <detail>` â€” never an empty payload â€” and records a
  `MISSION_TOOL_ERROR` event when the mission exists.
- Every mutating tool response ends with a `STATE:` proof (freshly re-read
  status / feature counts / last event), so success and failure are both
  self-verifying without a second `mission_status` round-trip.
- `features` / `milestones` / `changed_files` / `tests` now accept native lists
  as well as JSON strings (`_as_json`), removing the strict-string failure mode.
- `mission_plan` truncates the echoed plan at 12 KB with a pointer to
  `mission_plan_view`, so large plans cannot hit bridge truncation.

### H2 — planner is a first-class config role
- `Config` gained `planner: RoleConfig`; resolution is planner â†’ orchestrator â†’
  `GOOSE_PROVIDER`/`GOOSE_MODEL`. The planner leaf is dispatched with
  `--provider/--model` from that role.
- Unknown top-level config keys are reported at `mission_create` (readiness
  note + warning line) instead of being silently dropped by pydantic.
- The effective-config dump shows planner/worker/validator and warns when they
  resolve to different models.

### H3 — model-aware timeouts
- Defaults: `worker_timeout` 420 â†’ **900 s**, `semantic_timeout` 180 â†’ **600 s**
  (`planner_timeout` stays 600). Caps are inert for fast models.
- The create-time model preflight now records a concrete `suggested_config`
  delta (e.g. `worker_timeout â‰¥ 900`, budgets â‰¥ 600) when it flags the model,
  surfaced in readiness, `mission_status`, and applied with one call via the new
  `mission_apply_suggestions` tool (H10). Readiness also warns that
  SMALL-OUTPUT-BUDGET models tend to omit the final envelope (H7 #3).

### H4 — validator timeout â‰  quality failure
- `ValidationResult.timed_out` recorded end-to-end (`_parse_validation`,
  validation files, status rendering).
- A timed-out validation round is **infrastructure**: recorded via
  `VALIDATION_TIMEOUT`, **not counted** toward `max_correction_attempts`, retried
  once at **double** the budget (per-milestone `validation_infra_retries`), then
  the mission blocks with an explicit `validator timeout` reason.
- A failing verdict with **zero actionable findings** no longer burns no-op
  corrective cycles (`VALIDATION_INCONCLUSIVE` â†’ retry â†’ truthful block). This
  was the exact MS01 false-block cascade (entry 6).
- Same treatment for the final-validation phase (`m.validation_retries`).

### H5 — one config-precedence rule for every role
- `MissionController._semantic_for(mission)` builds the semantic client from the
  mission's effective config (env < repo file < mission overrides); the
  validation backend accepts a `(mission) -> client` factory. Explicitly
  injected clients (tests/host sampling) still win. Editing mission config now
  affects validation and planning, not just workers.

### H6 — run reporting
- `mission_run` responses end with `RUN REPORT: dispatches this call=N`
  (`max_steps` counts dispatches; auto-retries are not steps) and
  `ready/queued now=M [...]`, so a caller can tell progress from a stall.

### H7 — envelope-failure recovery
- New `FailureClass.ENVELOPE_FAILURE` (retryable): when the leaf output is
  unparseable but the feature branch carries commits/diff beyond base
  (`_worktree_evidence`), the run is classified as an envelope failure instead
  of `IMPLEMENTATION_FAILURE` (F002/F004/F005 pattern, entry 7).
- The retry prompt says the work is already written and demands only the JSON
  envelope.
- On budget exhaustion with git evidence, the feature is **accepted on git
  evidence** (`FEATURE_COMPLETED.accepted_on_git_evidence=true`) instead of
  being marked FAILED; milestone scrutiny still gates quality.
- `WORKER_FAILED` events now carry `worktree_commits`/`worktree_files`.

### H8 — event visibility
- `append_event` flushes + fsyncs each append so concurrent readers see events
  immediately; `mission_status` shows `Last event: <type> (<age>s ago)` to
  separate poll lag from a dead loop.

### H9 — scratch hygiene
- Worker prompts forbid scratch/debug files inside the repo.
- `_reconcile_result` strips root-level untracked scratch files (`_*`,
  `scratch*`, `*.tmp/.diff/.rej/.orig/.bak`) from the feature **worktree**
  before the reconcile commit, so junk never enters mission history; removals
  are recorded in `SCRATCH_CLEANED` events. Never touches the user's repo root.

### H11 — stale-mission housekeeping
- `mission_list` entries carry `terminal`, `age_days`, `stale`.
- New `mission_gc(max_age_days=7, archive=false)` lists terminal/stale missions
  and (with `archive=true`) cancels stale non-terminal ones; data is kept.

Verification: `pytest -m "not realgoose"` â€” **167 passed** (128 prior + 39 new
regression tests: `test_tool_layer.py`, `test_planner_config.py`,
`test_validation_resilience.py`, `test_envelope_recovery.py`,
`test_pipeline_integrity.py`).


### 9. Mission cancelled by user; MS01 work merged to main and validated
- Time: 2026-08-31 ~09:10 UTC (session end)
- Symptom: After MS01 validation false-blocked the mission (see entry 5), the scheduler's remaining paths were resume/re-validate with the new 900 s timeout, or external completion. User decision: cancel the mission and land the work directly.
- Action taken:
  1. Mission `M-2026-0142179C` cancelled; mission worktrees cleaned up.
  2. Mission/base work fast-forward merged to `main`: 9939feb..c2e32f6 (19 commits: F001–F005 feature commits + merges), then docs commit deab4ed (this file + `HAMGOOSE_ISSUES_REPORT.md` + `docs/meshy_like_4090_appstack.md`). Pushed to origin/main (remote URL migration notice printed but old URL still works).
  3. Validation on main: 62/62 backend tests pass; frontend production build passes (1.75 s, chunk-size warning only).
  4. MS01 (F001–F005) is therefore **implemented and on main** despite the mission being cancelled. MS02 (F006–F008) and MS03 (F009–F011) remain unimplemented.
- Status: closed. MS01 delivered; MS02/MS03 deferred.

### 8. Operational note: cmd.exe truncates multi-line `python -c` — use temp .py files
- Time: 2026-08-31, recurring during session
- Symptom: multi-line `python -c "..."` commands under cmd.exe silently truncated at the first newline; commands appeared to fail or run partial code.
- Adjustment: write temporary `.py` scripts to disk and execute them instead. Not a hamgoose issue — recorded here because it slowed diagnosis of the MCP/controller-call workarounds in entries 3 and 5.

### 7. Qwen3.8 envelope-failure pattern: work committed but final JSON completion envelope missing (F002, F004, F005)
- Time: 2026-08-31 ~07:50–08:25 UTC
- Symptom: F002, F004, and F005 each failed implementation on their first attempt with IMPLEMENTATION_FAILURE, even though the actual code work **was committed** to the worktree:
  - F002: attempt 1 ran 291 s (not a timeout) and committed `be7bf4f` — the full 409-line `backend/workers/hamstack_workers/pixal3d.py` worker — but the final JSON completion envelope was missing/malformed, so the controller classified the run as failed.
  - F004: attempt 1 same pattern (work landed, envelope missing); attempt 2 succeeded.
  - F005: attempts 1 and 2 both failed; the work was committed across `cc86d9d` and `6a4ab4a` (`jobs.py` +435 lines, `schemas.py` +49 lines, tests). The worker also left ~10 scratch files in the repo root (`_f005_*.py`, `.diff`, `.txt`). Attempt 3 succeeded and cleaned up its own scratch files.
- Root cause: Qwen3.8 is a SMALL-OUTPUT-BUDGET model: it will write and commit code, but frequently fails to emit the final structured JSON envelope the worker backend requires for a clean COMPLETED classification. The controller then burns a correction attempt on work that already exists on the branch.
- Adjustment: attempt 2/3 prompts were seeded with the previous failure evidence (what was committed, what the envelope check expected), which let the model converge quickly (F002 attempt 2: 82 s). For F005, the eventual success included self-cleanup of scratch files.
- Watch item: upstream fix should have the worker backend treat "commits present on worktree branch + envelope missing" as a recoverable/reclassifiable state rather than a clean failure (issues report H7). Also: workers should not commit scratch files to the repo (H9).
- Status: closed by retry; model-behavior risk remains for larger features.

### 6. MS01 validation false-fails: validator goose run dies at the 180 s semantic timeout → mission BLOCKED
- Time: 2026-08-31 ~08:30 UTC
- Symptom: MS01 validation produced `"validator produced no structured verdict"` (passed=false, severity=major, findings=[]) for **both** validation kinds (scrutiny and user_testing) — each goose run lasted **exactly 180.0 s** before being killed. The corrective loop creates fix features from findings, but there were none, so it made zero corrective features, burned all 3 `max_correction_attempts`, and transitioned the mission to BLOCKED — on a milestone whose code had passed 62/62 tests.
- Root cause (confirmed by reading `hamgoose/src/hamgoose/validator.py`, `semantic.py`, `config.py`): the validator backend runs a single `goose run` with `semantic_timeout=180 s` (default). Qwen3.8 needs more wall time to review a ~4 k-line milestone diff and emit the JSON verdict, so it was killed mid-answer; `_parse_validation` maps "no extractable JSON" to `ValidationResult(passed=False, severity="major", summary="validator produced no structured verdict")`. **Critical subtlety:** the validator's `SemanticClient` is built from **controller-level config** (`Config.load(repo=repo)`), NOT from the mission's `config` field. So editing `mission.json` would have had no effect on validation; only repo-level config works.
- Fix: wrote `D:\HamSTACK\.goose\hamgoose\config.json` (repo-level default) with `execution.semantic_timeout=900`, `worker_timeout=900`, `planner_timeout=600`. Every controller constructed afterwards picks it up, including the in-flight SemanticClient on next run. The in-flight correction loop kept 180 s in memory and blocked the milestone before it could re-validate.
- Disposition: with the mission blocked and the fix only taking effect on a fresh run, the user cancelled the mission instead of resuming (entry 9). If MS02/MS03 run via hamgoose later, start with this config file in place.
- Watch item: if 900 s is still not enough, or Qwen3.8 cannot emit the verdict JSON at all, options are raising it further or (with user sign-off) dropping a validation kind in config.
- Status: root cause fixed (repo-level config committed to worktree); mission itself closed by cancellation.

### 5. Worker 420 s timeout too short for Qwen3.8 → raised to 900 s; F001 completed externally
- Time: 2026-08-31 ~07:30 UTC
- Symptom: F001 worker attempts 1 and 2 both hit the 420 s `worker_timeout` with `timed_out=true`. Attempt 1 (WORKER_FAILED event at 07:13:14) had already committed the full 385-line `installer/wsl2/setup_pixal3d.sh` (commit `052731a`, merged to mission/base) but ran out of time before the model-manifest entry; attempt 2 produced no commit at all.
- Diagnosis: default `worker_timeout=420 s` is below the wall time Qwen3.8 (SMALL-OUTPUT-BUDGET) needs for multi-part features. Left unadjusted, every later feature (all larger than F001) would have burned its 3-attempt budget on timeouts. The timeout kill is done via `gosub.run_captured` → `taskkill /F /T /PID` on Windows.
- Adjustment (user directive: keep the mission going):
  1. `mission.json` config: `execution.worker_timeout` 420 → 900 (picked up by the next `mission_run`; no loop was in flight at edit time — safe to edit).
  2. F001 finished via the documented external-completion path: added the `installer/manifest_models.json` `pixal3d` entry (9 checkpoints with sha256, mirroring the script; total models 14 → 15) + `docs/SETUP.md` two-step provisioning section, committed to mission/base (`f19501e`), then `complete_feature_external` (via direct controller call — see entry 3; the MCP tool also no-oped here).
- Note: the event stream read **lagged ~5 min once** — the 07:13:14 WORKER_FAILED was not visible until ~07:18. Do not conclude the loop is dead from a single stale poll; verify with the process list and worktree state (issues report H8).
- Status: closed. F001 complete; timeout raised for all subsequent features.

### 4. mission_plan with explicit decomposition silently no-oped via MCP; mission_complete_feature same behavior
- Time: 2026-08-31 ~06:47 UTC
- Symptom: `mission_plan` called with explicit `features`/`milestones` JSON strings returned an **empty result** and recorded **no events** (mission stayed PLANNING). Later, `mission_complete_feature` via MCP also returned empty `{}` with no events.
- Diagnosis: the explicit path in `controller.plan()` bypasses the LLM entirely and works — verified by calling the controller directly in Python:
  ```python
  import sys; sys.path.insert(0, r'D:\hamgoose\src')
  from hamgoose.server import _controller as ctl
  ctl.plan(mission_id, features=features, milestones=milestones)
  ```
  which applied the plan (11 features / 3 milestones, status AWAITING_APPROVAL) immediately. Same pattern worked for `complete_feature_external()`. The MCP tool layer is swallowing the payload/exception (argument-size or exception-swallowing issue in the 0.1.8 tool bridge — issues report H1).
- Adjustment: for this session, any MCP hamgoose call that returns empty with no new events was retried as a direct controller call, with state verified via `mission_status`/`mission_events` afterwards.
- Open item: report the silent MCP no-op upstream in hamgoose (H1 is CRITICAL — the tool layer swallows errors with no stderr, no event, no nonzero signal).
- Status: closed for this session via workaround.

### 3. PLAN_FAILED (again): planner not pinned to custom_airouter
- Time: 2026-08-31 ~06:43 UTC
- Symptom: `mission_plan` on M-2026-0142179C returned an empty plan; PLAN_FAILED after 3 attempts, each with the same error: `"Rate limit exceeded: [1308][Usage limit reached for 5 hour. Your limit will reset at 2026-08-31 16:10:39]"`.
- Root cause: `mission_create` config in hamgoose 0.1.8 only accepts documented keys (worker/validator/execution/git/validation). The `planner` provider pin was **silently dropped**, so planner LLM calls still routed to the default/inherited provider under the 5-hour usage cap. The effective-config printout confirmed only worker + validator showed `custom_airouter`.
- Adjustment: bypass the planner LLM entirely by passing an explicit features/milestones decomposition to `mission_plan` (documented fallback) — which then hit entry 4, and was resolved by the direct controller call.
- Open item: pin the planner's provider via whatever config key hamgoose 0.1.8 actually honors (to be discovered upstream); until then, always pass explicit plans.
- Status: closed for this session via explicit-plan workaround.

---

## Mission M-2026-013808F8 — 2026-08-31

### 2. Mission cancelled: planner LLM pinned to a rate-limited provider
- Time: 2026-08-31 ~06:38 UTC
- Symptom: `mission_plan` returned an empty plan; PLAN_FAILED after 3 attempts.
- Error: `"Rate limit exceeded: [1308][Usage limit reached for 5 hour. Your limit will reset at 2026-08-31 16:10:39]"`
- Root cause: planner (and inherited worker/validator) LLM usage was not pinned to the intended provider and landed on a provider under a 5-hour usage cap. User had directed all LLM usage go through `custom_airouter` / `Qwen3.8`.
- Fix: mission cancelled and recreated as M-2026-0142179C with worker/validator/planner explicitly pinned to provider `custom_airouter`, model `Qwen3.8`; `max_concurrent_workers=2` per user rule (more than 2 concurrent subagents makes the provider drop all connections).
- Note: the planner pin was silently dropped on recreation (see entry 3) — the pinning fix worked for worker/validator only.
- Status: cancelled, superseded by M-2026-0142179C.

### 1. (context) Session rule: max 2 concurrent subagents
- Time: 2026-08-31, session start
- Constraint: provider drops **all** connections if more than 2 subagents run concurrently. `max_concurrent_workers=2` set on both missions. hamgoose default is also 2, but it was set explicitly to make the constraint durable in mission config.
- Status: standing constraint for any future mission in this repo.

---

## Cross-cutting observations (feed into HAMGOOSE_ISSUES_REPORT.md)

- **Config precedence gotcha (H5):** repo-level `.goose/hamgoose/config.json` affects controller-level components (SemanticClient → validator); per-mission `config` in `mission.json` does NOT. The two files now hold the same execution values (900/900/600) on purpose; keep them in sync.
- **Event-stream lag (H8):** one ~5 min poll lag observed (entry 5); correlate with process list + worktree state before concluding a stall.
- **Worker scratch files (H9):** F005 worker left ~10 `_f005_*` files in the repo root for two attempts before self-cleaning on attempt 3.
- **Sandbox/client timeout ≠ failure:** `mission_run(max_steps=2)` client calls timed out at 300 s in the MCP sandbox while the server loop kept running; poll `mission_events` for progress instead of re-issuing the run.
- **Client-side 300 s sandbox limit:** any hamgoose MCP call that can exceed ~5 minutes (full milestone runs) should be backgrounded server-side and polled, or issued via direct Python controller calls with a longer timeout.
