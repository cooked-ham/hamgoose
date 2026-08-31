"""MCP server layer for hamgoose.

Wires the deterministic MissionController to Goose via the official MCP surface:
Tools for state-changing operations, Resources for read-oriented mission data,
and Prompts for reusable mission workflows. All mission state lives on disk, so
the extension is stateless across calls and reconnects cleanly after a Goose
restart.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import FastMCP

from . import store
from .config import Config
from .controller import MissionController
from .models import Mission


def _repo(explicit: Optional[str] = None) -> str:
    return explicit or os.environ.get("GOOSE_REPOSITORY") or os.environ.get("HAMGOOSE_REPO") or os.getcwd()


def _cfg(config: Optional[Dict[str, Any]] = None) -> Config:
    return Config.load(config)


def _controller(repo: Optional[str] = None, config: Optional[Dict[str, Any]] = None) -> MissionController:
    return MissionController(_repo(repo), _cfg(config))


def _parse_config(config: Any) -> Optional[Dict[str, Any]]:
    if not config:
        return None
    if isinstance(config, str):
        try:
            return json.loads(config)
        except json.JSONDecodeError:
            return None
    return config


mcp = FastMCP("hamgoose")


# ========================================================================== #
# TOOLS (state-changing operations)
# ========================================================================== #
@mcp.tool()
def mission_create(
    goal: str,
    repo: Optional[str] = None,
    rules: str = "",
    config: Optional[Dict[str, Any]] = None,
) -> str:
    """Create a hamgoose mission. Use this to START a mission for the user.

    Guided setup protocol:
    1. If the user has not stated a clear goal, ask for it (one short question).
    2. Ask ONCE whether they have rules/constraints worth recording (e.g.
       concurrency limits, provider/model for workers, git on/off, validation
       toggles). If they say none, proceed with defaults - do not interrogate.
    3. Pass their rules VERBATIM in `rules` (persisted on the mission; shown in
       plan and status; given to every worker as context).
    4. Translate rules into `config` overrides with this map:
       - "max N concurrent agents/workers/subagents" -> {"execution": {"max_concurrent_workers": N}}
       - "one worker at a time" / "sequential"        -> {"execution": {"max_concurrent_workers": 1}}
       - "use <provider>/<model> for workers"         -> {"worker": {"provider": ..., "model": ...}}
       - "same for validators"                        -> {"validator": {"provider": ..., "model": ...}}
       - "no git / no worktrees"                      -> {"git": {"enabled": false, "use_worktrees": false}}
       - "skip user-facing testing"                   -> {"validation": {"user_testing": false}}
       - "no scrutiny validation"                     -> {"validation": {"scrutiny": false}}
    Note: workers are always isolated `goose run` leaf processes (never nested
    delegation); max_concurrent_workers caps how many run simultaneously.

    Returns the mission id, a readiness report and next steps.
    Next: mission_plan, present the plan, get approval, mission_approve, mission_run."""
    ctl = _controller(repo, _parse_config(config))
    m = ctl.create_mission(goal, _parse_config(config), rules=rules or None)
    out = "Mission created: {}\n\n".format(m.id)
    out += ctl.readiness(m.id) + "\n\n"
    out += "Next: call mission_plan to generate the structured plan.\n"
    return out


@mcp.tool()
def mission_plan(mission_id: str, repo: Optional[str] = None) -> str:
    """Generate the structured dependency-aware plan and present it for approval.
    No implementation happens until mission_approve is called."""
    ctl = _controller(repo)
    ctl.plan(mission_id)
    m = ctl._get(mission_id)
    return "PLAN (mission {}), status {}\n\n{}\n\nNext: call mission_approve to begin execution.".format(
        mission_id, m.status.value, ctl.plan_text(mission_id)
    )


@mcp.tool()
def mission_approve(mission_id: str, repo: Optional[str] = None) -> str:
    """Approve the plan and begin dependency-aware execution. Safe to call only
    once, from AWAITING_APPROVAL."""
    ctl = _controller(repo)
    ctl.approve(mission_id)
    return "Approved. Mission is RUNNING.\n\n" + ctl.status(mission_id) + "\n\nNext: call mission_run to execute."


@mcp.tool()
def mission_run(mission_id: str, repo: Optional[str] = None, max_steps: Optional[int] = None) -> str:
    """Advance the mission control loop (schedule, dispatch isolated workers,
    validate, correct). Resumable - call again to continue an in-progress mission."""
    ctl = _controller(repo)
    return ctl.run(mission_id, max_steps)


@mcp.tool()
def mission_pause(mission_id: str, repo: Optional[str] = None, reason: str = "") -> str:
    """Pause an active mission. No new workers are launched while paused."""
    ctl = _controller(repo)
    ctl.pause(mission_id, reason)
    return "Paused. " + (reason or "no reason given")


@mcp.tool()
def mission_resume(mission_id: str, repo: Optional[str] = None) -> str:
    """Resume a paused/blocked mission, reconciling repository and worker state."""
    ctl = _controller(repo)
    ctl.resume(mission_id)
    return "Resumed.\n\n" + ctl.status(mission_id)


@mcp.tool()
def mission_steer(
    mission_id: str,
    repo: Optional[str] = None,
    instruction: str = "",
    feature_id: Optional[str] = None,
    priority: Optional[int] = None,
) -> str:
    """Steer a running mission without rebuilding the plan. Optionally reprioritize
    a specific feature by id and priority."""
    ctl = _controller(repo)
    ctl.steer(mission_id, instruction, feature_id, priority)
    return "Steering recorded: " + (instruction or "reprioritize {}".format(feature_id))


@mcp.tool()
def mission_replan(mission_id: str, repo: Optional[str] = None, instruction: str = "") -> str:
    """Replan the remaining work around a new constraint. Preserves valid completed
    work, marks invalidated work superseded, and bumps the plan revision."""
    ctl = _controller(repo)
    ctl.replan(mission_id, instruction)
    m = ctl._get(mission_id)
    return "Replanned (revision {}), status {}.\n\n{}".format(m.current_revision, m.status.value, ctl.plan_text(mission_id))


@mcp.tool()
def mission_cancel(mission_id: str, repo: Optional[str] = None) -> str:
    """Cancel a mission and clean up its worktrees."""
    ctl = _controller(repo)
    ctl.cancel(mission_id)
    return "Cancelled."


@mcp.tool()
def mission_retry_feature(mission_id: str, feature_id: str, repo: Optional[str] = None) -> str:
    """Manually retry a failed/blocked feature."""
    ctl = _controller(repo)
    ctl.retry_feature(mission_id, feature_id)
    return "Feature {} reset to READY.".format(feature_id)


@mcp.tool()
def mission_validate(mission_id: str, kind: str = "scrutiny", repo: Optional[str] = None) -> str:
    """Run a validator now (scrutiny | user_testing | final). Returns a structured verdict."""
    ctl = _controller(repo)
    return ctl.validate(mission_id, kind)


# read-oriented tools (mirrored by Resources)
@mcp.tool()
def mission_status(mission_id: str, repo: Optional[str] = None) -> str:
    """Mission control status for a mission."""
    return _controller(repo).status(mission_id)


@mcp.tool()
def mission_plan_view(mission_id: str, repo: Optional[str] = None) -> str:
    """The current plan for a mission."""
    return _controller(repo).plan_text(mission_id)


@mcp.tool()
def mission_readiness(mission_id: str, repo: Optional[str] = None) -> str:
    """Readiness/preflight report for a mission."""
    return _controller(repo).readiness(mission_id)


@mcp.tool()
def mission_list(repo: Optional[str] = None) -> List[Dict[str, Any]]:
    """List all missions in the repository."""
    return _controller(repo).list()


@mcp.tool()
def mission_events(mission_id: str, repo: Optional[str] = None, tail: int = 30) -> List[Dict[str, Any]]:
    """Recent mission events (append-only event log)."""
    return store.read_events(_repo(repo), mission_id, tail)


# ========================================================================== #
# RESOURCES (read-oriented mission data)
# ========================================================================== #
def _mission_or_none(repo: str, mission_id: str) -> Optional[Mission]:
    return store.load_mission(repo, mission_id)


@mcp.resource("mission://{mission_id}/status")
def res_status(mission_id: str) -> str:
    m = _mission_or_none(_repo(), mission_id)
    return m.status.value if m else "mission not found"


@mcp.resource("mission://{mission_id}/plan")
def res_plan(mission_id: str) -> str:
    from . import render

    m = _mission_or_none(_repo(), mission_id)
    return render.plan_md(m) if m else "mission not found"


@mcp.resource("mission://{mission_id}/events")
def res_events(mission_id: str) -> str:
    import json as _json

    evs = store.read_events(_repo(), mission_id, 200)
    return _json.dumps(evs, indent=2)


@mcp.resource("mission://{mission_id}/features")
def res_features(mission_id: str) -> str:
    import json as _json

    m = _mission_or_none(_repo(), mission_id)
    if not m:
        return "mission not found"
    return _json.dumps({fid: f.to_dict() for fid, f in m.features.items()}, indent=2)


@mcp.resource("mission://{mission_id}/milestones")
def res_milestones(mission_id: str) -> str:
    import json as _json

    m = _mission_or_none(_repo(), mission_id)
    if not m:
        return "mission not found"
    return _json.dumps({mid: ms.to_dict() for mid, ms in m.milestones.items()}, indent=2)


@mcp.resource("mission://{mission_id}/validation")
def res_validation(mission_id: str) -> str:
    import json as _json

    m = _mission_or_none(_repo(), mission_id)
    if not m:
        return "mission not found"
    out = {"milestones": {}, "final": []}
    for mid, ms in m.milestones.items():
        out["milestones"][mid] = [
            {"kind": r.kind, "passed": r.passed, "severity": r.severity, "summary": r.summary,
             "findings": [f.__dict__ for f in r.findings]}
            for r in ms.validation
        ]
    out["final"] = [
        {"kind": r.kind, "passed": r.passed, "severity": r.severity, "summary": r.summary}
        for r in m.final_validation
    ]
    return _json.dumps(out, indent=2)


# ========================================================================== #
# PROMPTS (reusable mission workflows)
# ========================================================================== #
@mcp.prompt()
def start_mission(goal: str = "", rules: str = "") -> str:
    """Guided mission setup: ask for the goal and any rules, create the mission, generate the plan, and walk the user through approval."""
    g = goal or "(not provided - ask the user for the goal in one short question)"
    r = (
        rules
        or "(none provided - ask the user ONCE for any constraints worth recording, e.g. "
        "concurrency limits or provider/model choices; if they say none, proceed with defaults)"
    )
    return (
        "You are guiding the user through hamgoose mission setup. Keep it conversational: "
        "short questions, plain language, no jargon, no menu dumps.\n"
        "GOAL: {g}\n"
        "RULES/CONSTRAINTS: {r}\n"
        "Steps:\n"
        "1. If the goal is missing, ask for it (one question).\n"
        "2. If rules are missing, ask ONCE for constraints worth recording. Do not interrogate.\n"
        "3. Call mission_create with the goal, the rules verbatim in `rules`, and the config "
        "overrides derived from the rules per the mission_create tool description.\n"
        "4. Report the readiness result in one or two lines (only flag problems).\n"
        "5. Call mission_plan, summarize the plan (milestones, feature count, worker cap), "
        "and ask the user to approve or adjust.\n"
        "6. On approval: mission_approve, then mission_run. Report progress as milestones "
        "complete. If the mission BLOCKS or PAUSES, explain in plain language and ask how to proceed.\n"
        "7. When COMPLETED, summarize what changed and where the result lives (branch/commits).\n"
    ).format(g=g, r=r)


@mcp.prompt()
def plan_mission(goal: str = "") -> str:
    """Create a mission for the given goal and generate its structured plan for approval."""
    if not goal:
        return (
            "The user wants a hamgoose mission plan but has not given a goal. "
            "Ask for the goal in one short question. Once you have it, run mission_create "
            "(if no mission exists for it) then mission_plan and show the structured plan "
            "for approval."
        )
    return (
        "Create and generate a plan for a hamgoose mission with goal: \"{}\". "
        "Run mission_create then mission_plan and show the structured plan for approval."
    ).format(goal)


@mcp.prompt()
def resume_mission(mission_id: str = "") -> str:
    """Reconcile the given mission after a pause or restart, resume it, and continue running."""
    if not mission_id:
        return (
            "The user wants to resume a hamgoose mission but did not say which. Call "
            "mission_list, offer the active/paused ones in one short question, then check "
            "mission_status for the chosen one; if paused/blocked call mission_resume, "
            "then mission_run."
        )
    return (
        "Resume hamgoose mission {} (reconcile state after any restart). "
        "Check mission_status; if paused/blocked call mission_resume, then mission_run."
    ).format(mission_id)


@mcp.prompt()
def validate_milestone(mission_id: str = "") -> str:
    """Run scrutiny + user-testing validation on the active milestone of the given mission."""
    if not mission_id:
        return (
            "The user wants to validate a hamgoose milestone but did not say which mission. "
            "Call mission_list, ask which mission in one short question, then run "
            "mission_validate on it and report the structured verdict."
        )
    return (
        "Validate the active milestone of hamgoose mission {} by running mission_validate "
        "(scrutiny and user_testing) and reporting the structured verdict."
    ).format(mission_id)


# ========================================================================== #
# entry point
# ========================================================================== #
def get_extension() -> FastMCP:
    """Return the configured MCP server (used by tests and the CLI)."""
    return mcp


def main() -> None:
    import sys

    args = sys.argv[1:]
    if args and args[0] == "--transport":
        transport = args[1] if len(args) > 1 else "stdio"
    else:
        transport = "stdio"
    try:
        mcp.run(transport)
    except TypeError:
        mcp.run()


if __name__ == "__main__":
    main()
