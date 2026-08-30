# Mission: Build a Factory-Droid-style Mission system for Goose

I want you to extend Goose with a first-class **Mission orchestration system** inspired by the behavior and workflow of Factory Droid's current Missions feature.

This is NOT merely:

- a Todo list
- a Recipe
- a planner prompt
- a wrapper around `delegate()`
- a static YAML workflow
- a single agent that happens to use subagents

The goal is a durable orchestration layer for long, complex projects that can plan work, create dependency-aware features and milestones, run isolated Goose workers, validate their results, recover from failures, pause/resume/replan, and maintain persistent state until the overall goal has actually been achieved.

Call the project/extension **goose-missions** unless a more appropriate project-local naming convention already exists.

---

# 0. FIRST: INVESTIGATE GOOSE ITSELF

Before implementing anything, inspect the Goose installation/source/version available on this machine and determine the CURRENT supported mechanisms for:

- MCP extensions
- platform extensions if accessible
- Recipes
- custom agents
- the Summon extension
- `delegate`
- asynchronous delegates
- `load`
- Todo/task tracking
- MCP Apps
- Goose session persistence
- `goose run`
- `goose run --provider`
- `goose run --model`
- `goose run --max-turns`
- `goose run --output-format json`
- `goose run --output-format stream-json`
- session naming/resumption
- extension discovery
- project/repository context
- Git operations
- permissions and sandbox behavior

Do NOT assume an old Goose API from memory.

Inspect the locally installed Goose CLI help and, when practical, the current Goose source/documentation.

Document your findings before choosing the architecture.

Important known behavioral constraint: Goose delegated subagents are leaf workers. Nested delegation from a delegated subagent is intentionally restricted. Therefore Mission orchestration MUST remain in the parent/controller layer. Do not design an architecture that requires arbitrary worker → worker → worker delegation.

Prefer public/stable Goose interfaces where possible.

Do not modify Goose core unnecessarily if a standalone MCP extension + companion Recipe/agent/controller can achieve the desired behavior cleanly.

However, if a platform extension is genuinely necessary to achieve reliable orchestration, explain exactly why before choosing that route.

---

# 1. TARGET USER EXPERIENCE

The finished system should make this interaction possible:

```text
User:
Start a mission to migrate this application from X to Y while preserving behavior.

Mission:
Analyzes repository.
Asks useful planning questions where genuinely necessary.
Builds proposed mission plan.

Mission Plan
  Milestone 1 — Foundation
    F001 ...
    F002 ...
  Milestone 2 — Migration
    F003 ...
    F004 ...
  Milestone 3 — Cleanup
    F005 ...

Validation strategy:
  scrutiny validation: enabled
  user-flow validation: enabled

Estimated worker runs: ...
Estimated validation runs: ...

Awaiting approval.
```

No implementation work begins before the initial mission plan is approved.

Once approved:

```text
Mission status: RUNNING
Milestone: 1/3

F001 RUNNING
F002 RUNNING
F003 BLOCKED BY F001
F004 PENDING

Workers:
worker-F001 ...
worker-F002 ...
```

The system continues orchestrating until the mission:

- completes successfully,
- is paused,
- becomes genuinely blocked and needs user input,
- is cancelled,
- or exhausts a defined recovery/retry policy.

The user should be able to leave and later resume the mission without losing mission state.

---

# 2. CORE FACTORY-MISSION BEHAVIOR TO REPRODUCE

Factory's Mission model should be treated conceptually as three classes of agents:

## A. ORCHESTRATOR

The orchestrator owns the mission.

It does NOT normally implement individual features itself.

Its responsibilities are:

- understand the overall goal
- inspect the repository/project
- collaboratively build the mission plan
- identify relevant project instructions and skills
- decompose the goal into features
- establish feature dependencies
- organize features into meaningful milestones
- define success criteria
- define validation criteria
- determine what can run concurrently
- select ready work
- launch workers
- monitor workers
- receive worker results
- inspect actual resulting repository state
- track commits/diffs
- detect failed or stalled work
- retry intelligently
- create corrective/fix features when needed
- trigger milestone validation
- process validator findings
- replan if reality diverges from the original plan
- maintain mission state
- expose progress to the user
- pause
- resume
- accept steering instructions
- complete the mission only after final validation

The orchestrator is the authority for scheduling and state transitions.

Workers do not independently rewrite the Mission plan.

---

## B. FEATURE WORKERS

A feature worker receives a tightly scoped feature.

Each worker runs in an isolated/fresh Goose context.

It should receive only the context needed to accomplish the feature, including:

- overall mission objective
- milestone objective
- feature description
- dependencies already completed
- feature acceptance criteria
- known architectural constraints
- relevant repository instructions
- relevant AGENTS.md / .goosehints / skills
- paths/files likely involved
- required verification
- Git/worktree information
- explicit definition of what it MUST NOT change

The worker is expected to:

1. inspect relevant code
2. implement the feature
3. run appropriate tests/checks
4. inspect its own diff
5. correct obvious problems
6. provide a structured result
7. commit or otherwise produce an atomic identifiable changeset if Git integration is being used

A worker MUST NOT mark its own feature finally accepted merely because it claims success.

The orchestrator and validators determine acceptance.

---

## C. VALIDATORS

There should be two conceptually separate validation roles.

### Scrutiny Validator

This validator treats completed implementation skeptically.

It checks things such as:

- whether the feature really exists
- whether acceptance criteria are met
- correctness
- regressions
- architecture violations
- test coverage
- lint/type/build results
- security problems
- error handling
- edge cases
- incomplete implementations
- mocks/placeholders/TODOs masquerading as finished code
- integration with previously completed work

It should inspect the actual code/diff/repository rather than merely reading the worker's summary.

### User-Testing Validator

For applications that can be exercised, this validator verifies behavior from the user's perspective.

Possible mechanisms include:

- browser automation
- HTTP/API calls
- TUI automation
- CLI invocation
- integration scripts
- application startup scripts
- test fixtures
- screenshots or rendered output where supported

The point is:

**Do not trust "tests passed" as proof that the feature works for a user.**

User-facing validation should exercise the resulting application when practical.

Allow each validation class to be individually disabled in configuration.

---

# 3. PLANNING PHASE

Planning is a first-class phase, not something hidden inside execution.

Mission lifecycle should initially be:

```text
CREATED
→ ANALYZING
→ PLANNING
→ AWAITING_APPROVAL
→ RUNNING
```

During PLANNING, do not edit application source code.

Repository inspection is allowed.

Planning should produce a structured Mission specification.

Each proposed feature must be small enough that one isolated worker has a reasonable chance of completing it.

Do not create meaningless microtasks such as:

```text
create file
add import
write function
```

if those actions logically belong to one feature.

Likewise, do not create giant features such as:

```text
rewrite the backend
```

if they should be decomposed.

Features should represent independently understandable, verifiable units of progress.

---

# 4. FEATURE MODEL

Every feature should persist fields equivalent to:

```yaml
id: F001
title: Add persistent session storage
description: ...
milestone: M001

dependencies:
  - F000

status: pending

priority: 100

acceptance_criteria:
  - ...
  - ...

validation:
  required: true
  commands:
    - ...
  user_flows:
    - ...

scope:
  expected_paths:
    - ...
  prohibited_paths:
    - ...

attempts: 0
max_attempts: 3

worker:
  run_id: null
  session_id: null
  started_at: null
  completed_at: null
  model: null
  provider: null

git:
  branch: null
  worktree: null
  commits: []

result:
  summary: null
  changed_files: []
  tests: []
  notes: []
```

Exact serialization format may change, but the concepts must remain.

Suggested feature states:

```text
PENDING
READY
RUNNING
VERIFYING
COMPLETED
FAILED
BLOCKED
NEEDS_FIX
CANCELLED
SUPERSEDED
```

State transitions must be deliberate and persisted.

---

# 5. MILESTONE MODEL

Milestones are validation boundaries.

A milestone groups related features into a meaningful project checkpoint.

Example:

```text
Milestone 1
Foundation and data model

Milestone 2
API implementation

Milestone 3
Frontend integration

Milestone 4
Production hardening
```

A milestone should include:

- objective
- ordered/dependency-aware feature set
- entry requirements
- completion criteria
- validation criteria
- status
- scrutiny result
- user-testing result

Suggested states:

```text
PENDING
RUNNING
VALIDATING
PASSED
FAILED
BLOCKED
```

The next milestone should normally not advance past a failed validation gate.

---

# 6. DEPENDENCY-AWARE SCHEDULER

Build a real scheduler.

Represent feature dependencies as a DAG where practical.

A feature becomes READY only when all required dependencies are accepted.

The orchestrator should find READY features and schedule independent ones concurrently.

Configuration must contain:

```yaml
max_concurrent_workers: 2
```

Default this to **2**.

The value must be configurable.

Do NOT equate concurrency with correctness.

If two features could modify overlapping areas or otherwise conflict, schedule them sequentially even if their dependency graph technically allows parallel execution.

Add a conflict-risk check before parallel dispatch.

---

# 7. WORKER EXECUTION

Investigate which of these implementations is most reliable with CURRENT Goose and choose accordingly:

### Option A: Native parent-session delegation

Parent Mission orchestrator uses Goose's async `delegate()` facility.

### Option B: Managed Goose worker processes

Mission controller launches isolated workers with something conceptually similar to:

```bash
goose run \
  --provider <worker-provider> \
  --model <worker-model> \
  --max-turns <worker-max-turns> \
  --output-format json \
  ...
```

### Option C: Goose internal/platform API

Use only if legitimately required and supported.

Whichever architecture is chosen, workers must be individually identifiable and independently observable.

Do not fake worker isolation by asking one context to role-play several workers.

---

# 8. GIT IS THE SOURCE OF TRUTH FOR CODE MISSIONS

For Git repositories, use Git as the authoritative record of implementation changes.

Prefer isolated branches/worktrees for concurrently executing features if practical.

Conceptually:

```text
mission/base
mission/F001
mission/F002
mission/F003
```

or equivalent worktrees.

The exact implementation should respect existing repository state and must not destroy uncommitted user work.

Before Mission execution:

- inspect Git status
- identify current branch
- identify base commit
- detect dirty working state
- persist this information

Do not silently discard existing modifications.

When worker work completes, associate its result with identifiable commits/diffs.

The orchestrator should integrate changes deterministically.

Handle:

- merge conflicts
- stale worker branches
- overlapping edits
- failed commits
- worker exit without changes
- worker claiming completion with unexpected changes

A Mission must never declare a feature complete merely because a worker emitted "done."

Verify the repository.

---

# 9. MILESTONE VALIDATION

When all required features in a milestone are apparently complete:

```text
Milestone
RUNNING
→ VALIDATING
```

Run:

```text
Scrutiny Validator
User-Testing Validator
```

where enabled.

These should be fresh independent contexts.

Validators should receive:

- mission objective
- milestone objective
- feature acceptance criteria
- base revision
- resulting revision
- relevant test commands
- known user flows
- repository context

They should independently examine the actual result.

Their output should be structured.

Example:

```json
{
  "passed": false,
  "severity": "major",
  "findings": [
    {
      "feature": "F003",
      "criterion": "...",
      "problem": "...",
      "evidence": "...",
      "recommended_fix": "..."
    }
  ]
}
```

---

# 10. SELF-CORRECTION

Validation failures are not immediately fatal.

If validation identifies a correctable issue, the orchestrator should create one or more targeted corrective features.

For example:

```text
F007 Add OAuth login
    completed

Validation:
    FAIL — callback state is not persisted

Automatically create:

F007-FIX1
Persist and validate OAuth callback state
```

Corrective features enter the dependency graph like ordinary features.

After they complete, rerun the affected validation.

Set finite retry limits.

Do not endlessly ask workers to "try again."

A retry should include the prior failure evidence and change strategy.

After repeated failure, mark the feature/milestone BLOCKED and explain precisely why.

---

# 11. PAUSE, RESUME AND CRASH RECOVERY

Mission state must persist outside the LLM context.

Do not depend on conversation memory for Mission correctness.

Persist sufficient state after every meaningful transition.

A Mission interrupted by:

- user closing Goose
- terminal shutdown
- worker crash
- machine restart
- model/provider failure

must be resumable.

On resume:

1. load persisted Mission
2. inspect repository/Git reality
3. reconcile any workers that were RUNNING
4. determine whether work actually completed
5. recover known commits/results if possible
6. return interrupted tasks to READY when safe
7. continue from the last valid state

Use atomic persistence.

Prefer a design such as:

```text
<repo>/.goose/missions/
    <mission-id>/
        mission.yaml
        state.sqlite
        events.jsonl
        plan.md
        artifacts/
        workers/
        validation/
```

Exact structure is negotiable.

A small SQLite state database plus human-readable exported YAML/Markdown/event logs would be a strong design.

Do not commit transient Mission runtime logs unless explicitly configured.

---

# 12. EVENT LOG

Maintain an append-only Mission event stream.

Examples:

```text
MISSION_CREATED
REPOSITORY_ANALYZED
PLAN_GENERATED
PLAN_REVISED
PLAN_APPROVED
MILESTONE_STARTED
FEATURE_READY
WORKER_STARTED
WORKER_FINISHED
WORKER_FAILED
FEATURE_COMPLETED
FEATURE_RETRIED
VALIDATION_STARTED
VALIDATION_FAILED
FIX_FEATURE_CREATED
VALIDATION_PASSED
MILESTONE_COMPLETED
MISSION_PAUSED
MISSION_RESUMED
MISSION_REPLANNED
MISSION_COMPLETED
MISSION_CANCELLED
```

Every event should contain:

- timestamp
- mission ID
- event type
- relevant entity ID
- concise data payload

This allows deterministic status reconstruction and troubleshooting.

---

# 13. USER STEERING

Running Missions must remain steerable.

Support behavior conceptually equivalent to:

```text
mission pause
mission status
mission resume
mission steer "<instruction>"
mission replan "<instruction>"
mission cancel
```

The exact interface may instead be MCP tools or natural-language operations.

Important distinction:

### STEER

Changes implementation guidance or priority without necessarily rebuilding the whole plan.

Example:

```text
Prioritize the REST API before the web dashboard.
```

### REPLAN

Pause execution and deliberately revise the Mission graph.

Example:

```text
We are no longer using PostgreSQL. Replan the remaining work around SQLite.
```

Replanning must:

- preserve historical state
- preserve completed work where still valid
- identify invalidated features
- mark replaced work SUPERSEDED when appropriate
- create a new plan revision
- request approval if the scope changes materially

Never silently rewrite Mission history.

---

# 14. MISSION CONFIGURATION

Support per-Mission/default configuration including:

```yaml
orchestrator:
  provider: inherit
  model: inherit

worker:
  provider: inherit
  model: inherit
  max_turns: 100

validator:
  provider: inherit
  model: inherit
  max_turns: 100

execution:
  max_concurrent_workers: 2
  max_feature_attempts: 3
  worker_timeout: null

validation:
  scrutiny: true
  user_testing: true

git:
  enabled: true
  use_worktrees: true
  auto_commit_features: true
```

Use the actual settings supported by the installed Goose version.

Do not invent unsupported "reasoning effort" settings.

If Goose/provider APIs expose controllable reasoning effort, support it cleanly.

Otherwise omit it rather than pretending it works.

The important capability is separately configurable:

- orchestrator model
- feature-worker model
- validator model

---

# 15. PROJECT INSTRUCTIONS AND SKILLS

Mission workers must not lose project-specific behavior.

Before execution, discover relevant:

- AGENTS.md files
- .goosehints
- Goose skills
- custom agents
- Recipes
- repository docs
- test instructions
- lint/build conventions

Pass relevant context explicitly into workers.

Do NOT assume a delegated worker automatically inherits every piece of parent context.

Context should be intentionally assembled.

Keep worker prompts scoped enough that the feature itself remains prominent.

---

# 16. READINESS/PREFLIGHT CHECK

Implement a light Mission-readiness check.

It does not need to clone Factory's proprietary scoring system.

Before running a large code Mission, detect whether the project exposes:

- source control
- build command
- test command
- lint/typecheck where applicable
- dependency installation procedure
- app startup procedure
- useful logs
- integration/E2E tests
- user-facing QA mechanism
- AGENTS.md/project guidance
- dirty Git state

Report limitations.

Example:

```text
Mission readiness

Git                  PASS
Unit tests           PASS
Build command        PASS
App startup          PASS
User-flow automation WARN
Logs                 PASS

User-facing validation may be limited because no automated browser
or application-driving mechanism was discovered.
```

A warning should not automatically prohibit the Mission unless execution would be unsafe or impossible.

---

# 17. DRY-RUN PLAN VALIDATION

Before asking for plan approval, inspect the proposed plan itself.

Check:

- dependency cycles
- vague acceptance criteria
- impossible validation requirements
- oversized features
- meaningless micro-features
- missing integration work
- conflicting parallel tasks
- milestones with no useful validation boundary
- hidden requirements implied by the requested outcome

Correct planning problems before presenting the plan.

The user should approve a plan that the orchestrator itself believes is executable.

---

# 18. MISSION CONTROL OUTPUT

CLI/text support is mandatory.

A visual dashboard is desirable if CURRENT Goose MCP Apps can support it without compromising the core implementation.

Text status should provide something similar to:

```text
GOOSE MISSION CONTROL

Mission: M-2026-001
Goal: Replace legacy auth system
Status: RUNNING
Elapsed: 01:42:18

Milestone 2/4
API Migration

Progress: 8/17 features

RUNNING
F008 OAuth callback handling
F009 Token persistence

READY
F010 Logout flow

BLOCKED
F011 Account linking ← F008

COMPLETED
F001 F002 F003 F004 F005 F006 F007

Workers
W-17 F008 running  11m
W-18 F009 running   8m

Last validation
Milestone 1: PASSED

Recent events
...
```

If MCP Apps are sufficiently mature, create a Mission Control dashboard showing:

- overall progress
- milestone progress
- feature states
- dependency relationships
- worker status
- validator status
- event log
- pause button
- resume button
- cancel button
- steering input
- feature details
- commit/diff references

Do not allow dashboard work to delay or destabilize the orchestration core.

Core first, UI second.

---

# 19. REQUIRED MCP/USER OPERATIONS

Design a clean API.

Exact names may differ, but the system needs equivalents of:

```text
mission_create
mission_analyze
mission_plan
mission_get_plan
mission_revise_plan
mission_approve
mission_run
mission_pause
mission_resume
mission_status
mission_list
mission_get
mission_steer
mission_replan
mission_cancel
mission_retry_feature
mission_validate
mission_events
```

Avoid exposing dozens of tiny internal implementation functions to the model if fewer high-level tools are more reliable.

Keep the tool interface difficult for an LLM to misuse.

Validate all state transitions server-side.

For example, calling `mission_resume` on COMPLETED should return a meaningful error rather than corrupting state.

---

# 20. ORCHESTRATOR CONTROL LOOP

The Mission runner should behave roughly like this:

```text
while mission is RUNNING:

    reconcile repository and worker reality

    if active worker failed:
        classify failure
        retry/replan/block as appropriate

    collect completed workers

    verify resulting changes

    transition successful features toward COMPLETED

    if milestone implementation complete:
        run milestone validators

        if validation passes:
            close milestone
            advance

        else:
            create corrective features
            continue milestone

    compute dependency-ready features

    dispatch safe independent work
        while active_workers < max_concurrent_workers

    if nothing runnable:
        determine whether:
            mission completed
            mission blocked
            worker still running
            validation pending
            dependency graph invalid

    persist state

    emit events
```

This control logic should be deterministic code where reasonable.

Do not make the LLM responsible for remembering all Mission state.

Use the model for semantic decisions, planning, coding, review and replanning.

Use software for:

- persistence
- state transitions
- retries
- IDs
- dependency tracking
- scheduling limits
- timestamps
- process management
- event logging

---

# 21. FAILURE CLASSIFICATION

Distinguish at least:

```text
MODEL_FAILURE
PROVIDER_FAILURE
WORKER_TIMEOUT
WORKER_CRASH
IMPLEMENTATION_FAILURE
TEST_FAILURE
VALIDATION_FAILURE
MERGE_CONFLICT
DEPENDENCY_FAILURE
USER_BLOCKED
INFRASTRUCTURE_FAILURE
```

Different failures deserve different recovery behavior.

A provider HTTP error should not be treated as proof the feature implementation is defective.

A test failure should not be blindly retried with identical instructions.

A merge conflict requires repository reconciliation.

Record failure classifications in Mission history.

---

# 22. RESOURCE CONTROL

Missions can run for a long time.

Add guards against runaway behavior:

- max concurrent workers
- max attempts per feature
- max turns per worker
- optional worker timeout
- optional mission-level budget/run ceiling if practical
- cancellation support
- graceful shutdown
- subprocess cleanup

Never leave zombie Goose workers behind after cancellation.

---

# 23. SECURITY AND PERMISSIONS

Do not bypass Goose's existing security model simply to make Missions autonomous.

Respect:

- configured tool permissions
- sandbox configuration
- extension restrictions
- repository boundaries

Workers should get the tools they need, not automatically every possible dangerous capability.

Never print secrets into Mission logs.

Redact obvious API keys/tokens from persisted worker output.

---

# 24. ACCEPTANCE TEST SUITE

Do not declare this project complete until you test the Mission system itself.

Create a small fixture repository/application specifically for Mission integration testing.

Required scenarios:

### Scenario A — Planning gate

Create a Mission.

Verify:

- repository analyzed
- plan produced
- features/milestones persisted
- no implementation occurs before approval

### Scenario B — Dependencies

Create:

```text
F001
F002
F003 depends on F001
```

Verify F003 cannot run before F001 completes.

### Scenario C — Parallel workers

Create two independent features.

Verify up to two workers execute concurrently.

Verify the configured concurrency limit is never exceeded.

### Scenario D — Worker failure

Deliberately make one worker fail.

Verify:

- failure recorded
- attempt incremented
- appropriate retry occurs
- Mission itself does not corrupt or disappear

### Scenario E — Validator catches worker defect

Have a worker produce intentionally incomplete code.

Validator must reject it.

Mission should create corrective work.

Corrective work should run.

Validation should rerun.

### Scenario F — Pause/resume

Pause an active Mission.

Verify no new workers start.

Restart the Mission system.

Resume it.

Verify state is preserved.

### Scenario G — Process interruption

Terminate Mission controller while work is in progress.

Restart.

Verify state reconciliation.

### Scenario H — Steering

Change priority while Mission is active.

Verify scheduler obeys the updated priority.

### Scenario I — Replanning

Change a major requirement.

Verify:

- Mission pauses
- remaining plan changes
- completed valid work remains
- invalidated work is recorded
- history remains intact

### Scenario J — Git conflict

Create overlapping worker edits.

Verify conflict is detected and handled rather than silently overwriting changes.

### Scenario K — Final validation

Mission must not report COMPLETED until final required validation has passed.

---

# 25. TESTING YOUR GOOSE INTEGRATION

Because this is an extension to Goose itself, test using REAL Goose invocation wherever reasonably possible.

Do not stop at unit tests of the Mission database.

Test:

```text
Goose
→ Mission extension
→ orchestrator
→ actual worker Goose session
→ actual repository modification
→ validator
→ Mission completion
```

This is the important integration path.

Also test the case where a worker attempts nested delegation.

The Mission architecture must not rely on nested delegate access.

---

# 26. DOCUMENTATION

Produce:

```text
README.md
ARCHITECTURE.md
MISSION-LIFECYCLE.md
CONFIGURATION.md
TESTING.md
```

README should include the shortest possible working example.

Document:

- installation
- enabling the extension
- starting a Mission
- approving a Mission
- checking status
- pausing
- resuming
- steering
- replanning
- cancelling
- model configuration
- concurrency configuration
- validation options
- persistence location
- Git behavior
- limitations

---

# 27. IMPLEMENTATION PRIORITIES

Build in this order:

```text
1. Goose capability investigation
2. Architecture decision
3. Mission persistent data model
4. Mission state machine
5. Planning workflow
6. Approval gate
7. Dependency scheduler
8. Worker launching
9. Worker result reconciliation
10. Git isolation/integration
11. Scrutiny validation
12. User-testing validation
13. Corrective feature loop
14. Pause/resume
15. Crash recovery
16. Steering/replanning
17. CLI/MCP status surfaces
18. Integration test suite
19. Documentation
20. MCP App Mission Control dashboard if appropriate
```

Do not start with the UI.

---

# 28. DEFINITION OF DONE

This project is NOT done merely because:

- the extension loads
- an MCP tool exists
- one subagent can be launched
- a Recipe runs
- a plan can be generated
- a Todo list is displayed
- workers print successful summaries

It is done when this works:

```text
USER GOAL
   ↓
COLLABORATIVE PLAN
   ↓
STRUCTURED FEATURES + MILESTONES
   ↓
USER APPROVAL
   ↓
DEPENDENCY-AWARE ORCHESTRATION
   ↓
ISOLATED FEATURE WORKERS
   ↓
REAL CODE/ARTIFACT OUTPUT
   ↓
SCRUTINY VALIDATION
   +
USER-FACING VALIDATION
   ↓
AUTOMATIC CORRECTION WHEN NEEDED
   ↓
NEXT MILESTONE
   ↓
FINAL INTEGRATION VALIDATION
   ↓
MISSION COMPLETED
```

And throughout that process:

```text
persistent state
Git traceability
pause
resume
crash recovery
steering
replanning
worker inspection
bounded retries
configurable models
configurable concurrency
event history
```

must actually function.

---

# 29. IMPORTANT DESIGN PRINCIPLE

Build this as a real orchestration SYSTEM around Goose, not one enormous prompt asking Goose to behave like an orchestrator.

Prompts should express semantic intent.

Code should enforce orchestration mechanics.

The LLM should decide things such as:

- how to decompose a project
- how a feature should be implemented
- whether an architectural adjustment makes sense
- how to repair validation findings

The runtime should decide things such as:

- whether dependencies are satisfied
- how many workers may run
- which process belongs to which feature
- whether a transition is legal
- whether a retry limit has been reached
- where state is persisted
- which commit belongs to a worker
- whether a Mission is paused
- what happened before a restart

Do not entrust deterministic bookkeeping to conversational memory.

---

# 30. BEGIN

Begin by investigating the installed Goose environment and repository/source interfaces.

Create an architecture report before implementation containing:

```text
1. Goose version/environment discovered
2. Available extension architecture
3. Available delegation architecture
4. Available headless/session APIs
5. Persistence options
6. Worker-isolation strategy
7. Git/worktree strategy
8. Chosen Mission architecture
9. Components/files to create
10. Risks/limitations
11. Integration-test strategy
```

Then proceed to implementation without waiting for further permission unless you encounter a genuinely destructive or externally consequential operation that requires approval.

Do not reduce this mission into a simplistic Recipe or Todo wrapper.

Build the orchestration layer.