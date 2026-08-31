"""Prompt templates for isolated Goose workers, validators and semantic tasks.

Core principle: prompts express semantic intent; the orchestrator code enforces
orchestration mechanics. Every prompt that must return machine-readable data
ends with an explicit JSON schema the caller parses.
"""
from __future__ import annotations

import json

from .models import Feature, Mission

_WORKER_CONTRACT = """
OUTPUT CONTRACT
================
When you are finished, your FINAL message MUST be a single fenced JSON block
exactly matching this schema (no prose after it):

```json
{
  "status": "completed" | "failed" | "blocked",
  "summary": "one or two sentences on what you did",
  "changed_files": ["path", "..."],
  "tests": ["command you ran", "..."],
  "notes": ["anything the reviewer should know"],
  "blocked_reason": "only if status is blocked: what you need"
}
```
"""

_RULES = """
HARD RULES
==========
- Work ONLY within the working directory provided.
- Do NOT modify files outside the feature's allowed scope.
- You MUST NOT delegate, spawn sub-agents, or start nested goose sessions.
  You are a leaf worker; finish this single feature yourself.
- Be decisive: inspect the relevant files, implement the feature, then run the
  targeted verification commands and inspect your own diff. Do not explore
  unrelated parts of the repository or run the entire suite unless it is needed.
- Commit your changes with git (message: 'feat(<id>): <title>') when git is enabled.
- If you cannot complete the work, set status to "failed" or "blocked" and say why.
Do NOT claim success unless the acceptance criteria are actually met.
"""


def worker_prompt(mission: Mission, feature: Feature, git_info: dict, project_context: str) -> str:
    deps = "\n".join(
        "- {} ({})".format(d, mission.features[d].summary_or("completed"))
        for d in feature.dependencies
        if d in mission.features
    ) or "(none)"
    ac = "\n".join("- " + c for c in feature.acceptance_criteria) or "- (derive sensible criteria)"
    expected = ", ".join(feature.expected_paths) or "(the paths relevant to this feature)"
    prohibited = ", ".join(feature.prohibited_paths) or "(none)"
    cmds = "\n".join("  $ " + c for c in feature.validation_commands) or "  (use the project's normal build/test commands)"
    prior = ""
    if feature.attempts > 0 and feature.failure_detail:
        prior = (
            "\nPREVIOUS ATTEMPT FAILED (change your approach; do not repeat the same failure)\n"
            "  class: " + str(feature.failure) + "\n  detail: " + feature.failure_detail + "\n"
        )
    ms = mission.milestones.get(feature.milestone)
    return f"""You are a HAMGOOSE worker executing one tightly-scoped feature of a larger mission.

MISSION GOAL
{mission.goal}

CURRENT MILESTONE
{ms.objective if ms else ""}

FEATURE {feature.id}: {feature.title}
{feature.description}

COMPLETED DEPENDENCIES (already merged into the working tree)
{deps}

ACCEPTANCE CRITERIA
{ac}

SCOPE
  expected paths : {expected}
  prohibited paths : {prohibited}

VERIFICATION COMMANDS
{cmds}

PROJECT CONTEXT (build/test/conventions)
{project_context or "(none discovered)"}

USER CONSTRAINTS (stated by the user; respect them)
{mission.rules if mission.rules else "(none stated)"}

GIT
{json.dumps(git_info)}

{_RULES}{prior}
Implement the feature now. Keep the work and final summary focused on this
feature; do not spend turns narrating or researching unrelated code.{_WORKER_CONTRACT}"""


def scrutiny_prompt(mission: Mission, milestone_id: str, base: str, head: str, project_context: str) -> str:
    ms = mission.milestones.get(milestone_id)
    feats = [mission.features[f] for f in ms.features if f in mission.features] if ms else []
    lines = []
    for f in feats:
        ac = "\n".join("    - " + c for c in f.acceptance_criteria) or "    - (review quality)"
        lines.append(
            "  {} {}\n    status={}\n    changed: {}\n    acceptance:\n{}".format(
                f.id, f.title, f.status.value, ", ".join(f.result.changed_files) or "n/a", ac
            )
        )
    feat_block = "\n".join(lines)
    return f"""You are a HAMGOOSE SCRUTINY VALIDATOR. You distrust worker claims and verify reality.

MISSION GOAL
{mission.goal}

MILESTONE {milestone_id} OBJECTIVE
{ms.objective if ms else ""}

BASE REVISION: {base or "n/a"}
RESULT REVISION: {head or "n/a"}

Inspect the actual repository, prioritizing the changed files, their direct
dependencies, and the tests relevant to this milestone. Run targeted build or
test commands first; broaden the check only when the evidence requires it.
Do NOT trust the worker summaries below. For each feature verify:
- the change actually exists and matches the acceptance criteria
- correctness, error handling, edge cases
- no regressions, no architecture violations
- no placeholder/TODO code masquerading as finished work
- integration with earlier features

FEATURES IN THIS MILESTONE
{feat_block}

PROJECT CONTEXT
{project_context or "(none)"}

Return ONLY a single fenced JSON block:
```json
{{
  "passed": true|false,
  "severity": "none"|"minor"|"major"|"critical",
  "summary": "one or two sentences",
  "findings": [
    {{"feature": "F001", "criterion": "...", "problem": "...", "evidence": "...", "recommended_fix": "..."}}
  ]
}}
```
Set "passed": false if ANY acceptance criterion is unmet or a real defect exists.
"""


def user_test_prompt(mission: Mission, milestone_id: str, base: str, head: str, project_context: str) -> str:
    ms = mission.milestones.get(milestone_id)
    feats = [mission.features[f] for f in ms.features if f in mission.features] if ms else []
    flows = []
    for f in feats:
        for fl in f.user_flows:
            flows.append("  - [{}] {}".format(f.id, fl))
    flows_block = "\n".join(flows) or "  - exercise the primary user-visible behavior of this milestone"
    return f"""You are a HAMGOOSE USER-TESTING VALIDATOR. Validate from the user's perspective.

MISSION GOAL
{mission.goal}
MILESTONE OBJECTIVE
{ms.objective if ms else ""}

Start/exercise the application (CLI, API, browser, TUI, scripts - whatever fits)
and drive the primary user flow below once. Passing unit tests does NOT prove a
user-facing feature works. Keep the test focused on this milestone; do not do
unrelated exploratory testing.

USER FLOWS
{flows_block}

PROJECT CONTEXT
{project_context or "(none)"}

Return ONLY a single fenced JSON block:
```json
{{
  "passed": true|false,
  "severity": "none"|"minor"|"major"|"critical",
  "summary": "what you did and observed",
  "findings": [
    {{"feature": "F001", "criterion": "user flow description", "problem": "...", "evidence": "...", "recommended_fix": "..."}}
  ]
}}
```
"""


def decompose_prompt(goal: str, repo_summary: str, instructions: str, max_features: int) -> str:
    return f"""You are the HAMGOOSE PLANNER. Decompose the user's goal into a structured,
dependency-aware implementation plan for an isolated-worker execution system.

USER GOAL
{goal}

REPOSITORY ANALYSIS
{repo_summary or "(none)"}

PROJECT INSTRUCTIONS / CONVENTIONS
{instructions or "(none)"}

Produce the smallest complete plan: milestones should be meaningful
integration/validation boundaries, and each feature should be one focused worker
task. Use the repository analysis above; do not spend time reconstructing
unrelated history.
Each feature must be small enough for ONE isolated worker to complete reliably,
have concrete acceptance criteria, and define likely affected paths. Define
dependencies so the graph is acyclic. Do NOT create trivial micro-features
(create a file / add an import) nor giant vague ones (rewrite backend).
Keep titles, descriptions, and criteria concise (one or two sentences each).

Return ONLY a single fenced JSON block:
```json
{{
  "milestones": [
    {{"id": "MS01", "objective": "...", "completion_criteria": ["..."]}}
  ],
  "features": [
    {{
      "id": "F001", "title": "...", "description": "...", "milestone": "MS01",
      "dependencies": ["F000"], "priority": 100,
      "acceptance_criteria": ["..."],
      "validation_commands": ["..."],
      "user_flows": ["..."],
      "expected_paths": ["..."], "prohibited_paths": ["..."],
      "validation_required": true
    }}
  ]
}}
```
Use at most {max_features} features. Feature ids must be F001.. sequential and
dependency ids must reference existing feature ids (no self-deps, no cycles).
"""


def diagnose_prompt(feature: Feature, worker_output: str) -> str:
    return f"""A HAMGOOSE worker failed on feature {feature.id}: {feature.title}.
Classify the failure and prescribe a corrective approach for the next attempt.

ACCEPTANCE CRITERIA
{chr(10).join('- ' + c for c in feature.acceptance_criteria)}

WORKER OUTPUT (last portion)
{worker_output[-4000:]}

Return ONLY a fenced JSON block:
```json
{{
  "class": "MODEL_FAILURE|PROVIDER_FAILURE|WORKER_TIMEOUT|WORKER_CRASH|IMPLEMENTATION_FAILURE|TEST_FAILURE|VALIDATION_FAILURE|MERGE_CONFLICT|DEPENDENCY_FAILURE|USER_BLOCKED|INFRASTRUCTURE_FAILURE",
  "retryable": true|false,
  "diagnosis": "what went wrong",
  "recommended_strategy": "a concrete different approach for the next attempt"
}}
```
"""


def replan_prompt(mission: Mission, instruction: str) -> str:
    done = ["{} {} ({})".format(f.id, f.title, f.status.value) for f in mission.features.values()]
    return f"""You are the HAMGOOSE PLANNER revising a partially-executed mission.

ORIGINAL GOAL
{mission.goal}

NEW INSTRUCTION / CONSTRAINT
{instruction}

CURRENT FEATURES (preserve work that remains valid)
{chr(10).join('- ' + d for d in done) or "(none)"}

Produce a REVISED plan. Keep features that remain valid. Mark work invalidated by
the new constraint as superseded. Create replacement/new features for invalidated
or missing work. Keep the dependency graph acyclic.

Return ONLY a single fenced JSON block:
```json
{{
  "keep": ["F001"],
  "supersede": ["F002"],
  "remove": ["F003"],
  "new_features": [ {{"title": "...", "description": "...", "milestone": "MS01", "dependencies": [], "acceptance_criteria": []}} ],
  "new_milestones": [ {{"id": "MS99", "objective": "..."}} ],
  "note": "summary of what changed and why"
}}
```
"""


def final_validation_prompt(mission: Mission, base: str, head: str, project_context: str) -> str:
    return f"""You are the HAMGOOSE FINAL VALIDATOR for the entire mission.

MISSION GOAL
{mission.goal}

BASE REVISION: {base or "n/a"}
FINAL REVISION: {head or "n/a"}

Verify the mission goal end to end using the changed files, acceptance criteria,
and the primary user-facing behavior. Run targeted build/tests/lint checks first;
inspect unrelated areas only if they are implicated by the changes. Check for
regressions and placeholder code without doing an exhaustive repository tour.

PROJECT CONTEXT
{project_context or "(none)"}

Return ONLY a single fenced JSON block:
```json
{{
  "passed": true|false,
  "severity": "none"|"minor"|"major"|"critical",
  "summary": "...",
  "findings": [ {{"feature": "-", "criterion": "...", "problem": "...", "evidence": "...", "recommended_fix": "..."}} ]
}}
```
"""
