# hamgoose

**Factory-Droid-style Mission orchestration for Goose.**

`hamgoose` is a first-class Goose **extension** that turns a user goal into a
dependency-aware **Mission**: it analyzes the repo, builds a structured plan of
features + milestones + acceptance criteria, waits for your approval, then runs
**isolated Goose workers** in Git worktrees, **validates** the real results,
creates **corrective work** when validation fails, and drives the mission to a
validated `COMPLETED` state — while persisting state, tracking Git, and
supporting pause / resume / crash-recovery / steering / replanning.

```
USER GOAL → ANALYSIS → STRUCTURED PLAN → FEATURES+DEPS+MILESTONES → APPROVAL
  → DEPENDENCY-AWARE EXECUTION (isolated workers) → REAL CODE
  → SCRUTINY + USER-FACING VALIDATION → AUTOMATIC CORRECTION
  → NEXT MILESTONE → FINAL VALIDATION → MISSION COMPLETED
```

It is a **genuine Goose extension** (a standalone stdio MCP server built on the
official `mcp`/`FastMCP` model) — not a Recipe, Todo wrapper, or delegation
prompt. **Code enforces the orchestration mechanics; models do the semantic
reasoning.** See [`ARCHITECTURE_REPORT.md`](ARCHITECTURE_REPORT.md) for the
compliance analysis and design decisions.

## Shortest working example

```bash
# 1. Build & install the extension into a venv (or publish to PyPI / use uvx)
cd hamgoose
uv venv .venv
uv pip install -p .venv -e .

# 2. Register it with Goose (one command — see "Register with Goose" below)
```

```text
You: Start a hamgoose mission to migrate this app from X to Y while preserving behavior.

goose: (calls mission_create → mission_plan, shows the plan)
  HAMGOOSE MISSION PLAN ... Status: AWAITING_APPROVAL

You: Approve it.

goose: (calls mission_approve → mission_run; dispatches isolated workers,
       validates, corrects)
  HAMGOOSE MISSION CONTROL ... Status: COMPLETED
```

Everything below can also be driven by calling the MCP tools directly.

## Install

```bash
uv venv .venv
uv pip install -p .venv -e .          # or: uv pip install -p .venv .
# tests:  uv pip install -p .venv "pytest>=8" "pytest-asyncio>=0.23"
```

Requirements: Python ≥ 3.11, `goose` (≥ 1.40) on PATH, `git` (for Git missions).

## Register with Goose

hamgoose is a normal Goose STDIO extension — add it through Goose itself, no
repo wiring:

**Option 1 — Goose's own menu (recommended).** Run `goose configure`, go to
**Extensions → Add Extension**:

```text
Type:     STDIO
Name:     hamgoose
Command:  hamgoose                    (if on PATH)
          or: D:\hamgoose\.venv\Scripts\python.exe -m hamgoose
```

The same Extensions section can toggle or remove it later.

**Option 2 — one command** (same result, scriptable):

```bash
hamgoose register       # merges the stdio entry into Goose's config.yaml
                        # (auto-detects the path via `goose info`, keeps a .bak)
hamgoose unregister     # reverse it
```

(With the venv active; otherwise `.venv\Scripts\hamgoose register`.)

**Option 3 — per run, no config change:**

```bash
goose run -t "..." --with-extension "hamgoose:D:\hamgoose\.venv\Scripts\python.exe -m hamgoose"
```

Then start a **new session** and ask "what tools do you have?" — the
`mission_*` tools should be visible. Restart Goose after code changes.

<details>
<summary>What registration writes (manual option — edit config.yaml, path from `goose info`)</summary>

```yaml
extensions:
  hamgoose:
    enabled: true
    type: stdio
    name: hamgoose
    description: Mission orchestration for Goose
    command: D:\hamgoose\.venv\Scripts\python.exe -m hamgoose
```

</details>


## Starting a mission

Once the extension is registered, you just talk to Goose — no menu digging:

```text
You:   /start_mission            (or simply: "start a hamgoose mission")

goose: What's the goal?

You:   Migrate the auth module from session cookies to JWT.

goose: Any rules or constraints? (e.g. concurrency limits, provider/model,
       git, validation) If not, I'll use the defaults.

You:   My provider only allows 3 concurrent agents at a time.

goose: (mission_create -> readiness -> mission_plan)
       Plan: 2 milestones, 6 features, workers capped at 3 concurrent. Approve?

You:   Approve.

goose: (mission_approve -> mission_run)
       MS01 1/3 ... MS01 passed scrutiny ... MS02 2/3 ...
       Mission COMPLETED - changes on branch mission/base with per-feature commits.
```

How the rules work: they are recorded on the mission **verbatim** (shown in
every `mission_status` and plan view), translated into execution config
("max 3 concurrent agents" → `max_concurrent_workers: 3`), and handed to
**every worker** as context. To adjust mid-mission, just say so — Goose
steers (`mission_steer`) or replans (`mission_replan`) without losing
completed work.

## The lifecycle (tools)

| Operation | Tool |
|---|---|
| Create + analyze repo | `mission_create(goal, repo?, config?)` |
| Generate the plan (approval gate) | `mission_plan(mission_id)` |
| Approve & start | `mission_approve(mission_id)` |
| Execute the control loop (resumable) | `mission_run(mission_id, max_steps?)` |
| Pause / resume | `mission_pause` / `mission_resume` |
| Steer (priority/guidance) | `mission_steer(instruction, feature_id?, priority?)` |
| Replan (new constraint) | `mission_replan(instruction)` |
| Retry a feature / validate now | `mission_retry_feature` / `mission_validate(kind)` |
| Cancel | `mission_cancel` |
| Read status / plan / events / list | `mission_status` / `mission_plan_view` / `mission_events` / `mission_list` |

**Resources** (read): `mission://{id}/status|plan|events|features|milestones|validation`.
**Prompts**: `start_mission`, `plan_mission`, `resume_mission`, `validate_milestone`.

No implementation happens until `mission_approve`. Repository inspection during
planning is allowed.

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

`<repo>/.goose/hamgoose` should be git-ignored (a `.gitignore` snippet is in
CONFIGURATION.md). Your current branch is never modified; `mission/base`
accumulates the merged, validated result for you to merge.

## Test

```bash
uv pip install -p .venv "pytest>=8" "pytest-asyncio>=0.23"
.venv\Scripts\python -m pytest -m "not realgoose"   # fast, deterministic (no LLM)
.venv\Scripts\python -m pytest -m "realgoose"        # real Goose + LLM (slower)
```

See [TESTING.md](TESTING.md).

## Docs

- [ARCHITECTURE_REPORT.md](ARCHITECTURE_REPORT.md) — compliance + design decisions
- [ARCHITECTURE.md](ARCHITECTURE.md) — component architecture
- [MISSION-LIFECYCLE.md](MISSION-LIFECYCLE.md) — state machines & control loop
- [CONFIGURATION.md](CONFIGURATION.md) — config reference & registration
- [TESTING.md](TESTING.md) — test strategy

## Known limitations

See the end of CONFIGURATION.md and ARCHITECTURE_REPORT.md (conflict retry
semantics, path-overlap heuristic, real-LLM test flakiness, mcp<2 pin).
