# hamgoose

[![PyPI version](https://img.shields.io/pypi/v/hamgoose)](https://pypi.org/project/hamgoose/)
[![Python](https://img.shields.io/pypi/pyversions/hamgoose)](https://pypi.org/project/hamgoose/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Goose extension](https://img.shields.io/badge/Goose-extension-841697)](https://goose-docs.ai)

**Factory-Droid-style Mission orchestration for [Goose](https://github.com/block/goose).**

Type a goal, get a structured plan, approve it — then watch **isolated Goose
workers** build it in Git worktrees, validated by skeptical reviewers,
corrected automatically, and driven to a verified `COMPLETED` state.

```
USER GOAL → ANALYSIS → STRUCTURED PLAN → FEATURES + DEPS + MILESTONES → APPROVAL
  → DEPENDENCY-AWARE EXECUTION (isolated workers) → REAL CODE
  → SCRUTINY + USER-FACING VALIDATION → AUTOMATIC CORRECTION
  → FINAL VALIDATION → MISSION COMPLETED
```

hamgoose is a genuine Goose **extension** — a standalone stdio MCP server built
on the official `mcp`/`FastMCP` model, not a recipe, todo wrapper, or
delegation prompt. **Code enforces the orchestration mechanics; models do the
semantic reasoning.**

## Why it's different

- **Approval gate.** Nothing is implemented until you approve the plan.
- **Isolated leaf workers.** Each feature runs in its own `goose` subprocess
  inside a Git worktree — no nested delegation, crash containment, real diffs.
- **Dependency-aware scheduling.** A DAG of features with path-overlap conflict
  detection and a hard concurrency cap (your provider's limits, enforced in code).
- **Two validators.** *Scrutiny* distrusts the worker's claims and inspects the
  diff and tests; *user-testing* exercises the app from the user's perspective.
- **Automatic correction.** Failed validation becomes corrective features; the
  loop repeats (bounded) until the milestone passes.
- **Crash recovery.** Atomic JSON state + append-only event log — restart Goose
  mid-mission and it reconciles and continues.
- **Steering & replanning** mid-mission without losing completed work.
- **Secrets redacted** in every persisted artifact.

## Quickstart

```bash
pip install git+https://github.com/cooked-ham/hamgoose.git   # requires Python 3.11+
hamgoose register
```

Then, in **any** repository you're working in:

```text
$ goose
You:   /start_mission
goose: What's the goal?
You:   Migrate the auth module from session cookies to JWT.
goose: Any rules or constraints? (concurrency, provider/model, git, validation)
You:   My provider only allows 3 concurrent agents at a time.
goose: Plan: 2 milestones, 6 features, workers capped at 3 concurrent. Approve?
You:   Approve.
goose: MS01 1/3 … passed scrutiny … MS02 2/3 …
       Mission COMPLETED — changes on branch mission/base with per-feature commits.
```

Rules are recorded **verbatim** on the mission (visible in every status and
plan view), translated into execution config ("max 3 concurrent" →
`max_concurrent_workers: 3`), and handed to **every worker** as context.
Mid-mission you can just say "pause", "don't touch config files", or
"replan around X" — steering and replanning never lose completed work.

## Install

Requires `goose` (≥ 1.40) on your PATH and `git` (for Git missions).

### 1. From GitHub (recommended)

```bash
pip install git+https://github.com/cooked-ham/hamgoose.git
hamgoose register        # merges the stdio entry into Goose's config (auto .bak)
```

Pin a release once tags exist: `pip install "git+https://github.com/cooked-ham/hamgoose.git@v0.1.0"`.

### 2. Via Goose's own menu (no extra commands)

`goose configure` → **Extensions → Add Extension** → Type `STDIO`, Name
`hamgoose`, Command `hamgoose`. The same menu toggles or removes it later.

### 3. From a clone (development)

```bash
git clone https://github.com/cooked-ham/hamgoose.git
cd hamgoose
uv venv .venv
uv pip install -p .venv -e .
.venv/bin/hamgoose register      # or: .venv\Scripts\hamgoose register (Windows)
```

### 4. Per-run, no config change

```bash
goose run -t "..." --with-extension "hamgoose:python -m hamgoose"
```

**Uninstall:** `hamgoose unregister` and `pip uninstall -y hamgoose`.

## The lifecycle (tools)

| Operation | Tool |
|---|---|
| Create + analyze repo (guided setup) | `mission_create(goal, rules?, config?)` |
| Generate the plan (approval gate) | `mission_plan(mission_id)` |
| Approve & start | `mission_approve(mission_id)` |
| Execute the control loop (resumable) | `mission_run(mission_id, max_steps?)` |
| Pause / resume | `mission_pause` / `mission_resume` |
| Steer (priority / guidance) | `mission_steer(instruction, feature_id?, priority?)` |
| Replan (new constraint) | `mission_replan(instruction)` |
| Retry a feature / validate now | `mission_retry_feature` / `mission_validate(kind)` |
| Cancel | `mission_cancel` |
| Read status / plan / events / list | `mission_status` / `mission_plan_view` / `mission_events` / `mission_list` |

**Resources** (read): `mission://{id}/status|plan|events|features|milestones|validation`
**Prompts**: `start_mission` (the `/start_mission` walkthrough), `plan_mission`, `resume_mission`, `validate_milestone`

No implementation happens until `mission_approve`.

## Where state lives

```
<repo>/.goose/hamgoose/<mission-id>/
  mission.json     # canonical atomic state
  mission.yaml     # human-readable mirror
  plan.md          # plan mirror
  events.jsonl     # append-only event log
  workers/         # redacted worker transcripts
  validation/      # validation reports
  worktrees_base/  # mission/base worktree (merged result)
  worktrees/<F>    # per-feature worktrees
```

State is per-repo and git-ignorable; your current branch is never modified —
`mission/base` accumulates the merged, validated result for you to merge.
Add `/.goose/hamgoose/` to your repo's `.gitignore`.

## Development

```bash
git clone https://github.com/cooked-ham/hamgoose.git && cd hamgoose
uv venv .venv
uv pip install -p .venv -e ".[dev]"
.venv/bin/python -m pytest -m "not realgoose"   # fast, deterministic (no LLM)
.venv/bin/python -m pytest -m "realgoose"       # real Goose + LLM (slower)
```

## Docs

- [ARCHITECTURE_REPORT.md](ARCHITECTURE_REPORT.md) — design decisions & compliance analysis
- [ARCHITECTURE.md](ARCHITECTURE.md) — component architecture
- [MISSION-LIFECYCLE.md](MISSION-LIFECYCLE.md) — state machines & control loop
- [CONFIGURATION.md](CONFIGURATION.md) — config reference, registration, known limitations
- [TESTING.md](TESTING.md) — test strategy
- [PUBLISHING.md](PUBLISHING.md) — PyPI release runbook

## License

MIT — see [LICENSE](LICENSE).
