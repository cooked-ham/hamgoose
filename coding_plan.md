# Mission: Build `hamgoose`, a Factory-Droid-Style Mission System for Goose

Build a first-class **Mission orchestration extension for Goose** called:

`hamgoose`

The goal is to recreate the useful behavior of Factory Droid's Mission system inside Goose using Goose's officially supported extension architecture.

This must be a real orchestration system for long-running software projects, not:

* a glorified Todo list
* a Recipe with extra steps
* one giant orchestration prompt
* a thin wrapper around `delegate()`
* a static YAML workflow
* a single Goose context pretending to be multiple agents

The finished system should support:

```text
USER GOAL
   ↓
PROJECT ANALYSIS
   ↓
STRUCTURED PLAN
   ↓
FEATURES + DEPENDENCIES + MILESTONES
   ↓
USER APPROVAL
   ↓
DEPENDENCY-AWARE EXECUTION
   ↓
ISOLATED WORKERS
   ↓
REAL CODE / ARTIFACT OUTPUT
   ↓
SCRUTINY VALIDATION
   +
USER-FACING VALIDATION
   ↓
AUTOMATIC CORRECTIVE WORK
   ↓
NEXT MILESTONE
   ↓
FINAL VALIDATION
   ↓
MISSION COMPLETED
```

Throughout that process `hamgoose` must maintain:

* persistent Mission state
* Git traceability
* pause/resume
* crash recovery
* steering
* replanning
* bounded retries
* configurable models
* configurable concurrency
* event history
* dependency tracking
* validation history
* worker status
* milestone status

---

# 1. REQUIRED READING

Before designing or implementing anything, read the current official Goose custom-extension documentation:

https://goose-docs.ai/docs/tutorials/custom-extensions

This is **mandatory required reading**.

Treat the current official Goose documentation as authoritative over:

* pretrained knowledge
* older Goose examples
* third-party tutorials
* assumptions in this prompt
* remembered APIs
* outdated GitHub code

The architecture and implementation of `hamgoose` MUST adhere to the current officially supported Goose extension model.

Before implementation, explicitly verify from the documentation:

* how Goose custom extensions are structured
* Goose's MCP architecture
* MCP Tools
* MCP Resources
* MCP Prompts
* MCP Sampling
* extension transport mechanisms
* STDIO extension configuration
* extension registration/discovery
* supported SDK/runtime expectations
* extension development and testing workflow
* how extension errors are surfaced
* installation/reload behavior
* relevant permissions/security behavior
* current limitations affecting this project

Do not invent a proprietary Goose extension system.

Do not modify Goose core unless the officially supported extension architecture genuinely cannot provide a required capability.

If any core modification becomes necessary, document the reason before making it.

---

# 2. INVESTIGATE THE INSTALLED GOOSE ENVIRONMENT

After reading the official extension documentation, inspect the Goose installation/version available on this machine.

Determine the CURRENT supported behavior of:

* MCP extensions
* MCP Tools
* MCP Resources
* MCP Prompts
* MCP Sampling
* MCP Apps
* Recipes
* custom agents
* Summon
* `delegate`
* asynchronous delegation
* `load`
* Todo/task tracking
* Goose session persistence
* `goose run`
* provider selection
* model selection
* maximum turn controls
* structured JSON output
* streaming structured output
* session naming
* session resumption
* extension discovery
* repository context
* Git operations
* permissions
* sandbox behavior

Use this authority order:

1. current official Goose documentation
2. locally installed Goose CLI help
3. source corresponding to the installed Goose version
4. current official Goose GitHub repository

Do not build against remembered or outdated Goose behavior.

---

# 3. IMPORTANT GOOSE SUBAGENT CONSTRAINT

Treat delegated Goose subagents as **leaf workers** unless the current installed Goose implementation explicitly proves otherwise.

Do NOT design:

```text
Orchestrator
  ↓
Worker
  ↓
Worker's Worker
  ↓
Nested Worker
```

Prefer:

```text
Mission Orchestrator
  ├── Worker F001
  ├── Worker F002
  ├── Worker F003
  ├── Scrutiny Validator
  └── User-Testing Validator
```

The Mission orchestrator/controller remains responsible for:

* scheduling
* dependencies
* worker dispatch
* validation
* retries
* persistence
* state transitions
* replanning

Workers should not become independent orchestrators.

---

# 4. PRE-IMPLEMENTATION ARCHITECTURE REPORT

Before implementation, create:

```text
HAMGOOSE ARCHITECTURE REPORT

1. Official Goose extension documentation findings
2. Installed Goose version/environment
3. Supported MCP capabilities
4. Extension registration mechanism
5. Transport choice
6. SDK/runtime choice
7. Tools/Resources/Prompts design
8. MCP Sampling decision
9. Worker isolation mechanism
10. Delegation capabilities and limitations
11. Headless Goose capabilities
12. Session persistence behavior
13. Mission persistence architecture
14. Git/worktree strategy
15. Scheduler architecture
16. Validator architecture
17. Crash-recovery architecture
18. Security/permission implications
19. Components/files to create
20. Integration-test strategy
21. Known risks

OFFICIAL GOOSE EXTENSION COMPLIANCE

PASS/FAIL

Any deviations:
...

Implementation may proceed:
YES/NO
```

If compliance is `FAIL`, correct the architecture before implementation.

Do not stop after producing the report.

Once the architecture is valid, proceed directly with implementation.

---

# 5. ARCHITECTURAL PRINCIPLE

`hamgoose` should be a genuine Goose extension using the official supported architecture.

Expected general structure:

```text
                    Goose
                      │
                      │ MCP
                      ▼
                   hamgoose
                      │
        ┌─────────────┼─────────────┐
        │             │             │
   Mission DB     Scheduler     Git Manager
        │             │             │
        ├────── Worker Manager ─────┤
        │                           │
        ├──── Validator Manager ────┤
        │                           │
        └──── Event / Recovery ─────┘
```

The extension should own deterministic orchestration mechanics.

LLMs should handle semantic reasoning.

## Code should control

* Mission IDs
* feature IDs
* milestone IDs
* state transitions
* dependency satisfaction
* concurrency
* retries
* timestamps
* persistent storage
* worker process/session tracking
* Git/worktree tracking
* event logging
* pause state
* timeout behavior
* crash recovery
* legality of operations

## Models should decide

* how to decompose the project
* how features should be implemented
* architectural choices
* corrective implementation strategies
* validation interpretation
* replanning decisions
* semantic failure diagnosis

Core principle:

**Prompts express semantic intent. Code enforces orchestration mechanics.**

Do not rely on conversational memory for deterministic Mission state.

---

# 6. USE MCP CAPABILITIES INTENTIONALLY

Do not expose every operation as a random MCP Tool.

Use Goose-supported MCP capabilities according to their intended purpose.

## MCP Tools

Use primarily for state-changing operations such as:

```text
mission_create
mission_plan
mission_approve
mission_run
mission_pause
mission_resume
mission_steer
mission_replan
mission_cancel
mission_retry_feature
mission_validate
```

The exact names may differ, but equivalent functionality must exist.

## MCP Resources

Where supported and useful, expose read-oriented Mission data such as:

```text
mission://<id>/status
mission://<id>/plan
mission://<id>/events
mission://<id>/features
mission://<id>/milestones
mission://<id>/validation
```

## MCP Prompts

Where appropriate, expose reusable Mission workflows such as:

```text
start-mission
plan-mission
resume-mission
validate-milestone
```

## MCP Sampling

Investigate whether MCP Sampling is useful for semantic operations such as:

* feature decomposition
* plan analysis
* validation interpretation
* failure diagnosis
* replanning

Do not automatically build the entire system around Sampling.

Compare it against:

* parent Goose orchestration
* isolated `goose run` workers
* Goose delegation

Choose the cleanest and most reliable design.

Document the decision.

---

# 7. TARGET USER EXPERIENCE

A user should be able to say something like:

```text
Start a mission to migrate this application from X to Y
while preserving current behavior.
```

`hamgoose` should:

1. analyze the repository
2. identify relevant project instructions
3. determine build/test/startup mechanisms
4. ask planning questions only when genuinely necessary
5. create a structured Mission plan
6. group work into milestones
7. define feature dependencies
8. define acceptance criteria
9. define validation criteria
10. present the plan for approval

Example:

```text
HAMGOOSE MISSION PLAN

Mission:
Migrate application from X to Y while preserving behavior.

Milestone 1 — Foundation
  F001 ...
  F002 ...

Milestone 2 — Migration
  F003 ...
  F004 ...

Milestone 3 — Integration
  F005 ...

Validation:
  Scrutiny validation: enabled
  User-facing validation: enabled

Estimated worker runs: ...
Estimated validation runs: ...

Status: AWAITING APPROVAL
```

No application implementation should occur before the initial plan is approved.

Repository inspection during planning is allowed.

---

# 8. MISSION LIFECYCLE

Support an explicit Mission state machine.

At minimum:

```text
CREATED
ANALYZING
PLANNING
AWAITING_APPROVAL
RUNNING
PAUSED
BLOCKED
VALIDATING
COMPLETED
FAILED
CANCELLED
```

Transitions must be deliberate, validated and persisted.

Example:

```text
CREATED
→ ANALYZING
→ PLANNING
→ AWAITING_APPROVAL
→ RUNNING
→ VALIDATING
→ COMPLETED
```

Do not allow impossible or corrupt transitions such as resuming an already completed Mission.

---

# 9. THE ORCHESTRATOR

The Mission orchestrator owns the overall Mission.

It should normally NOT implement individual features itself.

Responsibilities:

* understand the user goal
* analyze the repository
* discover project instructions
* create the Mission plan
* define milestones
* define features
* establish dependencies
* define acceptance criteria
* determine parallelizable work
* schedule ready features
* launch workers
* monitor workers
* reconcile results
* inspect actual repository state
* track commits/diffs
* classify failures
* retry intelligently
* create corrective features
* run milestone validation
* process validator results
* pause/resume
* accept steering
* replan when necessary
* determine final Mission completion

The orchestrator is the authority for scheduling and state transitions.

Workers must not rewrite the Mission plan independently.

---

# 10. FEATURE MODEL

Each feature should represent one understandable and verifiable unit of progress.

Avoid useless microtasks such as:

```text
create file
add import
write helper
```

when those actions logically belong to one feature.

Also avoid giant vague features such as:

```text
rewrite backend
```

Features should be small enough for one isolated worker to have a reasonable chance of completing successfully.

Persist fields conceptually equivalent to:

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
  provider: null
  model: null

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

---

# 11. MILESTONES

Milestones should be meaningful integration and validation boundaries.

Example:

```text
Milestone 1
Foundation and data model

Milestone 2
Backend implementation

Milestone 3
Frontend integration

Milestone 4
Production hardening
```

Each milestone should track:

* objective
* features
* dependencies
* entry requirements
* completion criteria
* scrutiny-validation status
* user-testing status
* overall status

Suggested states:

```text
PENDING
RUNNING
VALIDATING
PASSED
FAILED
BLOCKED
```

Do not automatically advance to the next milestone after failed required validation.

---

# 12. DEPENDENCY-AWARE SCHEDULER

Represent feature dependencies as a DAG where practical.

A feature becomes `READY` only when all required dependencies have been accepted.

The scheduler should:

1. determine ready features
2. determine whether any ready features conflict
3. schedule safe independent work concurrently
4. respect the concurrency ceiling
5. wait for dependencies when required

Default:

```yaml
max_concurrent_workers: 2
```

This MUST be configurable.

Two workers is the default because the system needs to behave well with providers that limit concurrent requests.

Do not blindly parallelize everything that lacks an explicit dependency.

Before parallel dispatch, consider:

* overlapping files
* shared components
* schema migrations
* shared configuration
* architectural dependency
* likely merge-conflict risk

Schedule conflicting work sequentially.

---

# 13. FEATURE WORKERS

Each worker receives a tightly scoped feature in a fresh or isolated Goose context.

A worker should receive only relevant context, including:

* overall Mission objective
* current milestone objective
* feature objective
* completed dependencies
* acceptance criteria
* architectural constraints
* repository instructions
* relevant AGENTS.md
* relevant `.goosehints`
* relevant skills
* relevant Recipes
* likely affected paths
* prohibited paths
* required test/build commands
* Git/worktree information

The worker should:

1. inspect relevant code
2. implement the feature
3. run appropriate verification
4. inspect its own diff
5. correct obvious defects
6. produce a structured result
7. create an identifiable changeset/commit when Git integration is enabled

Workers must NOT mark themselves finally accepted simply because they claim success.

Acceptance belongs to the Mission orchestrator and validators.

---

# 14. WORKER EXECUTION STRATEGY

Investigate and choose the most reliable mechanism supported by CURRENT Goose.

Possible approaches:

## A. Native delegation

Parent orchestrator uses Goose delegation.

## B. Managed isolated Goose processes

Conceptually:

```bash
goose run \
  --provider <provider> \
  --model <model> \
  --max-turns <limit> \
  --output-format json \
  ...
```

Use actual supported current arguments.

## C. Supported Goose internal/platform interface

Use only if publicly supported and clearly preferable.

Whichever architecture is chosen:

* workers must be isolated
* workers must be individually identifiable
* worker output must be observable
* workers must not rely on nested delegation
* crashes must be detectable
* workers must be cancellable

Do not simulate multiple workers inside one conversational context.

---

# 15. GIT AS SOURCE OF TRUTH

For Git repositories, Git should be the authoritative record of implementation changes.

Before execution:

* inspect Git status
* detect current branch
* determine base commit
* detect uncommitted user changes
* persist repository state

Never discard existing user modifications.

For concurrent features, prefer isolated branches/worktrees when practical.

Conceptually:

```text
mission/base
mission/F001
mission/F002
mission/F003
```

Associate worker results with actual commits/diffs.

Verify:

* expected files changed
* unexpected files were not changed
* commits exist when expected
* worker output matches repository reality

Handle:

* merge conflicts
* stale branches
* overlapping edits
* failed commits
* workers exiting without meaningful changes
* workers claiming success with incorrect changes

A worker saying `done` is not evidence that a feature is complete.

Inspect the repository.

---

# 16. VALIDATORS

Use two conceptually separate validation roles.

## Scrutiny Validator

This validator should distrust worker claims and inspect the actual result.

Check:

* feature actually exists
* acceptance criteria
* correctness
* regressions
* architecture violations
* tests
* lint/typechecking/build
* security problems
* error handling
* edge cases
* incomplete implementation
* placeholder code
* TODOs masquerading as finished work
* integration with earlier features

It should inspect code/diffs/repository state, not merely worker summaries.

## User-Testing Validator

Where the application can be exercised, validate it from the user's perspective.

Possible mechanisms:

* browser automation
* CLI execution
* API calls
* integration tests
* startup scripts
* HTTP testing
* TUI automation
* screenshots/rendered output
* application-driving tools

The principle is:

**Passing unit tests does not automatically prove that a user-facing feature works.**

Allow each validation type to be enabled/disabled in configuration.

---

# 17. MILESTONE VALIDATION

When all required features in a milestone appear complete:

```text
RUNNING
→ VALIDATING
```

Run fresh validator contexts.

Validators should receive:

* Mission objective
* milestone objective
* relevant feature acceptance criteria
* base revision
* resulting revision
* test/build commands
* expected user flows
* repository context

Return structured results such as:

```json
{
  "passed": false,
  "severity": "major",
  "findings": [
    {
      "feature": "F003",
      "criterion": "OAuth callback state must persist",
      "problem": "Callback state is stored only in memory",
      "evidence": "...",
      "recommended_fix": "Persist state in the configured session store"
    }
  ]
}
```

---

# 18. AUTOMATIC CORRECTIVE WORK

Validation failure should not immediately terminate a Mission.

When a validator finds a correctable defect, create one or more targeted fix features.

Example:

```text
F007 Add OAuth login
COMPLETED

Validation:
FAIL
Callback state is not persisted.

Create:

F007-FIX1
Persist and validate OAuth callback state.
```

Corrective features should participate in the dependency graph like normal features.

After corrective work completes:

* rerun affected validation
* keep finite retry limits
* include prior failure evidence in retry context
* change strategy rather than blindly repeating identical instructions

After repeated failure, mark the feature or milestone `BLOCKED` and explain precisely why.

Never retry forever.

---

# 19. FAILURE CLASSIFICATION

Distinguish at minimum:

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

Recovery behavior should depend on the failure.

Examples:

* provider HTTP failure does not prove implementation failure
* test failure should result in corrective coding context
* merge conflict requires Git reconciliation
* worker crash may be safely retryable
* missing user information may require `USER_BLOCKED`

Persist the classification in Mission history.

---

# 20. PAUSE, RESUME AND CRASH RECOVERY

Mission state must persist outside the model context.

A Mission should survive:

* closing Goose
* terminal shutdown
* worker crash
* Goose restart
* machine restart
* provider failure

On resume:

1. load persisted Mission
2. inspect repository reality
3. reconcile workers previously marked `RUNNING`
4. determine whether work actually completed
5. recover known commits/results
6. return interrupted work to `READY` when safe
7. continue from the last valid state

Use atomic persistence.

A design similar to this is reasonable:

```text
<repo>/.goose/hamgoose/
    <mission-id>/
        mission.yaml
        state.sqlite
        events.jsonl
        plan.md
        artifacts/
        workers/
        validation/
```

Exact structure may differ.

A small SQLite database plus human-readable YAML/Markdown/event logs is preferred if appropriate.

Do not commit runtime logs into the user's repository unless explicitly configured.

---

# 21. EVENT LOG

Maintain an append-only Mission event history.

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
MISSION_STEERED
MISSION_REPLANNED
MISSION_COMPLETED
MISSION_CANCELLED
```

Each event should include:

* timestamp
* Mission ID
* event type
* related entity ID
* concise structured payload

This should make debugging and state reconstruction possible.

---

# 22. STEERING AND REPLANNING

Running Missions must remain steerable.

Support equivalents of:

```text
mission pause
mission status
mission resume
mission steer "<instruction>"
mission replan "<instruction>"
mission cancel
```

Natural-language invocation through Goose is acceptable as long as reliable MCP operations exist underneath it.

## STEER

Changes implementation guidance or scheduling priority without necessarily rebuilding the Mission plan.

Example:

```text
Prioritize the API before the dashboard.
```

## REPLAN

Deliberately revises remaining Mission structure.

Example:

```text
We are no longer using PostgreSQL.
Replan the remaining work around SQLite.
```

Replanning should:

* pause scheduling
* preserve Mission history
* preserve completed work that remains valid
* identify invalidated work
* mark replaced work `SUPERSEDED`
* create a new plan revision
* request approval again if the scope changes materially

Never silently rewrite Mission history.

---

# 23. CONFIGURATION

Support sensible defaults with per-Mission override.

Conceptually:

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

Use actual settings supported by current Goose.

Do not invent unsupported settings.

If provider/model-specific reasoning effort is supported, expose it cleanly.

If not, omit it.

The important requirement is separate configuration for:

* orchestrator model
* worker model
* validator model

---

# 24. PROJECT INSTRUCTIONS AND SKILLS

Before planning/execution, discover relevant project-specific instructions:

* AGENTS.md
* `.goosehints`
* Goose skills
* Recipes
* custom agents
* repository documentation
* build instructions
* test instructions
* lint conventions
* startup procedures

Pass only relevant context to each worker.

Do not assume isolated workers automatically inherit everything from the parent session.

Context must be intentionally assembled.

---

# 25. READINESS / PREFLIGHT

Before running a significant Mission, inspect project readiness.

Report:

```text
HAMGOOSE READINESS

Git                  PASS
Build command        PASS
Unit tests           PASS
Lint/typecheck       PASS
App startup          PASS
User-flow automation WARN
Project instructions PASS
Dirty working tree   WARN
```

Check for:

* source control
* build command
* test command
* lint/typecheck
* dependency installation
* application startup
* useful logging
* integration/E2E tests
* browser/user-flow automation
* project guidance
* dirty Git state

Warnings should not automatically block execution unless they make the Mission unsafe or impossible.

---

# 26. PLAN VALIDATION

Before presenting the plan for approval, inspect the plan itself.

Check for:

* dependency cycles
* vague features
* oversized features
* meaningless micro-features
* vague acceptance criteria
* impossible validation requirements
* missing integration work
* dangerous parallel conflicts
* poor milestone boundaries
* hidden requirements implied by the user's goal

Fix plan defects before asking for approval.

---

# 27. MISSION CONTROL

CLI/text status is mandatory.

Example:

```text
HAMGOOSE MISSION CONTROL

Mission: M-2026-001
Goal: Replace legacy authentication
Status: RUNNING

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
W-17  F008  running
W-18  F009  running

Validation
Milestone 1: PASSED

Recent events
...
```

If current Goose MCP Apps support it cleanly, optionally add a visual Mission Control dashboard showing:

* Mission progress
* milestone progress
* feature states
* dependency relationships
* worker status
* validation status
* event history
* pause/resume
* cancel
* steering input
* feature detail
* commit/diff references

Do NOT prioritize UI over orchestration correctness.

Core runtime first.

UI second.

---

# 28. ORCHESTRATOR CONTROL LOOP

The runtime should conceptually behave like:

```text
while mission is RUNNING:

    reconcile repository and worker reality

    process finished workers

    classify failures

    verify resulting changes

    transition successful features

    if milestone implementation is complete:
        run validators

        if validation passes:
            complete milestone
        else:
            create corrective features

    compute dependency-ready work

    while active_workers < max_concurrent_workers:
        dispatch safe independent features

    if nothing can run:
        determine whether:
            mission is complete
            mission is blocked
            workers are still active
            validation is pending
            dependency graph is invalid

    persist state

    append events
```

Keep this control flow deterministic where practical.

---

# 29. RESOURCE CONTROL

Prevent runaway Missions.

Support:

* maximum concurrent workers
* maximum feature attempts
* maximum turns per worker
* optional worker timeout
* graceful cancellation
* graceful shutdown
* subprocess cleanup
* optional Mission-level resource/budget ceiling if practical

Never leave zombie Goose worker processes after cancellation.

---

# 30. SECURITY

Respect Goose's existing security model.

Do not bypass:

* tool permissions
* sandbox settings
* extension restrictions
* repository boundaries

Workers should receive only necessary capabilities.

Do not persist secrets into Mission logs.

Redact obvious:

* API keys
* access tokens
* passwords
* bearer tokens
* credentials

from stored worker output.

---

# 31. INTEGRATION TEST SUITE

Do not declare `hamgoose` complete based only on unit tests.

Create a small fixture project and test the real orchestration path.

Required scenarios:

## A. Planning gate

Verify:

* repository analyzed
* plan created
* features/milestones persisted
* no implementation occurs before approval

## B. Dependencies

Create:

```text
F001
F002
F003 depends on F001
```

Verify F003 cannot run before F001.

## C. Parallel workers

Create two independent features.

Verify both can run concurrently.

Verify the default concurrency limit of 2 is never exceeded.

## D. Worker failure

Deliberately cause a worker failure.

Verify:

* failure recorded
* attempt incremented
* recovery/retry occurs
* Mission state remains valid

## E. Validator catches defect

Have a worker create intentionally incomplete implementation.

Verify:

* validator rejects it
* corrective feature is created
* corrective worker runs
* validation reruns

## F. Pause/resume

Pause an active Mission.

Verify:

* no new workers launch
* state persists
* restart `hamgoose`
* Mission resumes correctly

## G. Process interruption

Terminate the Mission controller while work is active.

Restart.

Verify state reconciliation.

## H. Steering

Change priorities while running.

Verify scheduler honors updated priorities.

## I. Replanning

Change a major requirement.

Verify:

* execution pauses
* plan revision occurs
* valid completed work remains
* invalid work becomes superseded where appropriate
* history is preserved

## J. Git conflict

Cause overlapping changes.

Verify conflict is detected and resolved safely rather than overwritten.

## K. Final validation

Mission must not report `COMPLETED` until all required final validation passes.

## L. Nested delegation constraint

Verify worker execution does not depend on worker-created nested delegates.

---

# 32. TEST THROUGH REAL GOOSE

Testing must include the actual Goose integration path:

```text
Goose
  ↓
hamgoose
  ↓
Mission orchestrator
  ↓
real isolated Goose worker
  ↓
real repository modification
  ↓
validator
  ↓
Mission completion
```

Also verify:

1. `hamgoose` starts correctly
2. Goose discovers it
3. Goose discovers its MCP capabilities
4. Mission operations work from an actual Goose session
5. Goose can restart
6. `hamgoose` reconnects correctly
7. Mission state survives restart

Do not consider standalone MCP-server testing sufficient.

---

# 33. DOCUMENTATION

Produce at minimum:

```text
README.md
ARCHITECTURE.md
MISSION-LIFECYCLE.md
CONFIGURATION.md
TESTING.md
```

README should contain the shortest possible working example.

Document:

* installation
* Goose registration
* starting a Mission
* plan approval
* status inspection
* pause
* resume
* steering
* replanning
* cancellation
* model configuration
* concurrency configuration
* validation configuration
* persistence location
* Git/worktree behavior
* crash recovery
* known limitations

---

# 34. IMPLEMENTATION ORDER

Build in roughly this order:

```text
1. Read official Goose extension docs
2. Inspect installed Goose
3. Produce architecture/compliance report
4. Create hamgoose extension skeleton
5. Persistent Mission data model
6. Mission state machine
7. Planning workflow
8. Approval gate
9. Dependency scheduler
10. Worker launching
11. Worker result reconciliation
12. Git/worktree integration
13. Scrutiny validator
14. User-facing validator
15. Corrective feature loop
16. Pause/resume
17. Crash recovery
18. Steering
19. Replanning
20. MCP Resources/Prompts refinements
21. Mission Control CLI/status
22. Integration test suite
23. Documentation
24. MCP App dashboard if appropriate
```

Do not start with the dashboard.

---

# 35. DEFINITION OF DONE

`hamgoose` is NOT complete merely because:

* the extension loads
* MCP tools exist
* a plan can be generated
* a Recipe works
* a Todo list is displayed
* one worker can be launched
* workers can print success messages

It is complete when this actually works:

```text
USER REQUEST
   ↓
ANALYSIS
   ↓
PLAN
   ↓
FEATURE DAG
   ↓
MILESTONES
   ↓
USER APPROVAL
   ↓
AUTOMATIC DEPENDENCY-AWARE EXECUTION
   ↓
ISOLATED WORKERS
   ↓
REAL CHANGES
   ↓
INDEPENDENT VALIDATION
   ↓
AUTOMATIC CORRECTION
   ↓
MILESTONE GATES
   ↓
FINAL VALIDATION
   ↓
COMPLETED MISSION
```

And throughout the lifecycle the Mission can reliably:

```text
persist
pause
resume
recover
steer
replan
retry
validate
track Git changes
track workers
track dependencies
record events
```

---

# 36. BEGIN

Start by:

1. reading the official Goose custom-extension documentation
2. inspecting the installed Goose version and capabilities
3. creating the `HAMGOOSE ARCHITECTURE REPORT`
4. selecting the simplest architecture that fully adheres to Goose's supported extension model
5. proceeding directly into implementation once compliance passes

Do not reduce this project into a simplistic Recipe, Todo wrapper or delegation prompt.

Build the actual orchestration layer.

**Project name: `hamgoose`.**
