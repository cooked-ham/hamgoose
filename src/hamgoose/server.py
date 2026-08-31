"""MCP server layer for hamgoose.

Wires the deterministic MissionController to Goose via the official MCP surface:
Tools for state-changing operations, Resources for read-oriented mission data,
and Prompts for reusable mission workflows. All mission state lives on disk, so
the extension is stateless across calls and reconnects cleanly after a Goose
restart.
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.server import Context

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


def _as_json(value: Any, what: str) -> Any:
    """H1: accept JSON strings OR native lists/dicts. A strict string-only
    contract turned bridge type quirks into silent ValueError deaths."""
    if value is None or isinstance(value, (list, dict)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError as e:
            raise ValueError("{} must be JSON (list/dict) or a JSON string: {} ({})".format(
                what, value[:80], e))
    raise ValueError("unsupported type for {}: {}".format(what, type(value).__name__))


def _state_proof(ctl: MissionController, mission_id: str) -> str:
    """H1 #2: every mutating tool ends its response with freshly re-read
    state, so success can NEVER masquerade as an empty result and callers do
    not need a second verification round-trip."""
    try:
        m = ctl._get(mission_id)
        evs = store.read_events(ctl.repo, mission_id, tail=1)
        last = "{}@{}".format(evs[-1].get("type"), (evs[-1].get("ts") or "")[:19]) if evs else "none"
        done = sum(1 for f in m.features.values() if f.status.value == "COMPLETED")
        return "STATE: mission={} status={} features={}/{} completed last_event={}".format(
            m.id, m.status.value, done, len(m.features), last)
    except Exception as e:
        return "STATE: unavailable ({})".format(e)


def _tool_error(ctl: MissionController, mission_id: str, exc: Exception) -> str:
    """H1 #1: a failing tool NEVER returns an empty/None payload. The error is
    returned as text (bridges that swallow exceptions then still deliver
    something informative), recorded as an event when the mission exists, and
    terminated with a state proof so the caller can tell what actually
    happened on disk."""
    try:
        m = store.load_mission(ctl.repo, mission_id)
        if m:
            store.append_event(m, "MISSION_TOOL_ERROR", entity=mission_id, payload={
                "error": "{}: {}".format(type(exc).__name__, exc)})
    except Exception:
        pass
    return "TOOL_ERROR: {}: {}\n{}".format(
        type(exc).__name__, exc, _state_proof(ctl, mission_id))


async def _call_with_progress(
    ctl: MissionController,
    method: str,
    ctx: Optional[Context],
    *args: Any,
    **kwargs: Any,
) -> Any:
    """Run a blocking controller method in a worker thread, forwarding its
    progress reports to the client as MCP progress notifications so long tool
    calls (plan/run/validate) show live activity instead of a silent wait."""
    fn = getattr(ctl, method)
    if ctx is None:
        return await asyncio.to_thread(fn, *args, **kwargs)

    loop = asyncio.get_running_loop()
    q: "asyncio.Queue" = asyncio.Queue()

    def _cb(message: str, current: float, total: float) -> None:
        # Queue is drained on the event loop thread; the callback runs on the
        # worker thread, so marshal through call_soon_threadsafe.
        loop.call_soon_threadsafe(q.put_nowait, (message, current, total))

    ctl.set_progress(_cb)
    fut = asyncio.ensure_future(asyncio.to_thread(fn, *args, **kwargs))
    while not fut.done():
        try:
            message, current, total = await asyncio.wait_for(q.get(), timeout=0.5)
        except asyncio.TimeoutError:
            continue
        try:
            await ctx.report_progress(current, total, message)
        except Exception:
            # No live MCP request backing the context (in-process tests) or the
            # client declined progress: notifications are best-effort.
            pass
    return await fut


mcp = FastMCP("hamgoose")


# ========================================================================== #
# TOOLS (state-changing operations)
# ========================================================================== #
@mcp.tool()
async def mission_create(
    goal: str,
    repo: Optional[str] = None,
    rules: str = "",
    config: Optional[Dict[str, Any]] = None,
    ctx: Optional[Context] = None,
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
       - "same for validators"                        -> {"validator": {"provider": ..., "model": ...}}
       - "use <provider>/<model> for planning"        -> {"planner": {"provider": ..., "model": ...}}
       - "no git / no worktrees"                      -> {"git": {"enabled": false, "use_worktrees": false}}
       - "skip user-facing testing"                   -> {"validation": {"user_testing": false}}
       - "no scrutiny validation"                     -> {"validation": {"scrutiny": false}}
    Note: workers are always isolated `goose run` leaf processes (never nested
    delegation); max_concurrent_workers caps how many run simultaneously.

    Returns the mission id, a readiness report and next steps.
    Next: mission_plan, present the plan, get approval, mission_approve, mission_run."""
    ctl = _controller(repo, _parse_config(config))
    m = await _call_with_progress(
        ctl,
        "create_mission",
        ctx,
        goal,
        _parse_config(config),
        rules=rules or None,
    )
    out = "Mission created: {}\n\n".format(m.id)
    out += ctl.readiness(m.id) + "\n\n"
    out += ctl.config_summary(m.id) + "\n\n"
    cfg_obj = Config.load(_parse_config(config))
    if cfg_obj.unrecognized_keys:
        # H2: a dropped pin (config.planner in 0.1.8) must be loud, not fatal.
        out += ("WARNING - ignored unknown config keys: {} (supported: worker, "
                "validator, planner, orchestrator, execution, validation, git)\n\n".format(
                    ", ".join(cfg_obj.unrecognized_keys)))
    out += "Next: call mission_plan to generate the structured plan.\n"
    return out


@mcp.tool()
async def mission_plan(mission_id: str, repo: Optional[str] = None, features: Optional[str] = None, milestones: Optional[str] = None, ctx: Optional[Context] = None) -> str:
    """Generate the structured dependency-aware plan and present it for approval.
    No implementation happens until mission_approve is called.

    If the planner returns an empty plan (goal too vague), retry mission_plan - or
    pass your OWN decomposition (JSON string or list):
    features='[{"id":"F001","title":"...","description":"...","milestone":"MS01",
    "dependencies":[],"acceptance_criteria":["..."],"expected_paths":["..."]}]'
    milestones='[{"id":"MS01","objective":"...","completion_criteria":["..."]}]'."""
    ctl = _controller(repo)
    try:
        await _call_with_progress(ctl, "plan", ctx, mission_id,
                                  features=_as_json(features, "features"),
                                  milestones=_as_json(milestones, "milestones"))
        m = ctl._get(mission_id)
        plan_text = ctl.plan_text(mission_id)
        if len(plan_text) > 12000:
            plan_text = plan_text[:12000] + "\n[...truncated - full plan via mission_plan_view]"
    except Exception as e:
        return _tool_error(ctl, mission_id, e)
    return ("PLAN (mission {}), status {}\n\n{}\n\n{}\n\nNext: call mission_approve to begin "
            "execution.").format(mission_id, m.status.value, plan_text, _state_proof(ctl, mission_id))


@mcp.tool()
def mission_approve(mission_id: str, repo: Optional[str] = None) -> str:
    """Approve the plan and begin dependency-aware execution. Safe to call only
    once, from AWAITING_APPROVAL."""
    ctl = _controller(repo)
    try:
        ctl.approve(mission_id)
    except Exception as e:
        return _tool_error(ctl, mission_id, e)
    return ("Approved. Mission is RUNNING.\n\n" + ctl.status(mission_id)
            + "\n\n" + _state_proof(ctl, mission_id)
            + "\n\nNext: call mission_run to execute.")


@mcp.tool()
async def mission_run(mission_id: str, repo: Optional[str] = None, max_steps: Optional[int] = None, ctx: Optional[Context] = None) -> str:
    """Advance the mission control loop (schedule, dispatch isolated workers,
    validate, correct). Resumable - call again to continue an in-progress mission.
    max_steps counts DISPATCHES; auto-retries inside a dispatch consume the
    feature's attempt budget, not a step. If your client sandbox times this call
    out, the loop keeps running server-side: poll mission_events instead of
    re-issuing. The response ends with a RUN REPORT (dispatches done, queued
    work) plus a STATE proof line."""
    ctl = _controller(repo)
    try:
        out = await _call_with_progress(ctl, "run", ctx, mission_id, max_steps=max_steps)
    except Exception as e:
        return _tool_error(ctl, mission_id, e)
    return out + "\n\n" + _state_proof(ctl, mission_id)


@mcp.tool()
def mission_pause(mission_id: str, repo: Optional[str] = None, reason: str = "") -> str:
    """Pause an active mission. No new workers are launched while paused."""
    ctl = _controller(repo)
    try:
        ctl.pause(mission_id, reason)
    except Exception as e:
        return _tool_error(ctl, mission_id, e)
    return "Paused. {}\n\n{}".format(reason or "no reason given", _state_proof(ctl, mission_id))


@mcp.tool()
def mission_resume(mission_id: str, repo: Optional[str] = None) -> str:
    """Resume a paused/blocked mission, reconciling repository and worker state."""
    ctl = _controller(repo)
    try:
        ctl.resume(mission_id)
    except Exception as e:
        return _tool_error(ctl, mission_id, e)
    return "Resumed.\n\n" + ctl.status(mission_id) + "\n\n" + _state_proof(ctl, mission_id)


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
    try:
        ctl.steer(mission_id, instruction, feature_id, priority)
    except Exception as e:
        return _tool_error(ctl, mission_id, e)
    return "Steering recorded: {}\n\n{}".format(
        instruction or "reprioritize {}".format(feature_id), _state_proof(ctl, mission_id))


@mcp.tool()
def mission_replan(mission_id: str, repo: Optional[str] = None, instruction: str = "") -> str:
    """Replan the remaining work around a new constraint. Preserves valid completed
    work, marks invalidated work superseded, and bumps the plan revision."""
    ctl = _controller(repo)
    try:
        ctl.replan(mission_id, instruction)
        m = ctl._get(mission_id)
    except Exception as e:
        return _tool_error(ctl, mission_id, e)
    return "Replanned (revision {}), status {}.\n\n{}\n\n{}".format(
        m.current_revision, m.status.value, ctl.plan_text(mission_id), _state_proof(ctl, mission_id))


@mcp.tool()
def mission_cancel(mission_id: str, repo: Optional[str] = None) -> str:
    """Cancel a mission and clean up its worktrees."""
    ctl = _controller(repo)
    try:
        ctl.cancel(mission_id)
    except Exception as e:
        return _tool_error(ctl, mission_id, e)
    return "Cancelled.\n\n" + _state_proof(ctl, mission_id)


@mcp.tool()
def mission_retry_feature(mission_id: str, feature_id: str, repo: Optional[str] = None) -> str:
    """Manually retry a failed/blocked feature. The retry counts toward the
    feature's attempt budget (attempts + manual_retries >= max_attempts stops
    further automated retries)."""
    ctl = _controller(repo)
    try:
        m = ctl.retry_feature(mission_id, feature_id)
        f = m.features.get(feature_id)
    except Exception as e:
        return _tool_error(ctl, mission_id, e)
    return ("Feature {} reset to READY (attempts={}, manual_retries={}, cap={}).\n\n{}").format(
        feature_id, f.attempts, f.manual_retries, f.max_attempts, _state_proof(ctl, mission_id))


@mcp.tool()
def mission_complete_feature(
    mission_id: str,
    feature_id: str,
    summary: str,
    commit: Optional[str] = None,
    changed_files: Optional[str] = None,
    tests: Optional[str] = None,
    repo: Optional[str] = None,
) -> str:
    """Record work on a feature that was implemented OUTSIDE the worker pipeline
    (by you, the lead agent, or a human). Use this instead of editing mission
    state files by hand. Verifies the commit exists, runs the feature's
    validation_commands, runs a real scrutiny validation on the diff, appends
    proper events, and continues the normal milestone flow.

    Args:
        summary: what was implemented and why it meets the acceptance criteria.
        commit: the git hash of the implementation (required for git missions).
        changed_files: JSON list of paths (auto-derived from the commit if omitted).
        tests: JSON list of verification commands you ran and their outcome.

    NOTE (H1): this runs a REAL scrutiny validation and can take minutes. If
    your client sandbox times the call out, the completion still proceeds
    server-side - verify via mission_events / mission_status instead of
    re-calling blindly.
    """
    ctl = _controller(repo)
    try:
        m = ctl.complete_feature_external(
            mission_id, feature_id, summary,
            changed_files=_as_json(changed_files, "changed_files"),
            tests=_as_json(tests, "tests"),
            commit=commit,
        )
        f = m.features.get(feature_id)
        v = m.milestones.get(f.milestone).validation[-1] if f and f.milestone in m.milestones and m.milestones[f.milestone].validation else None
        verdict = "passed" if (v and v.passed) else "REVIEW (scrutiny did not pass - findings recorded)"
    except Exception as e:
        return _tool_error(ctl, mission_id, e)
    return "Feature {} completed externally ({}). Scrutiny {}. Status: {}.\n\n{}\n\n{}".format(
        feature_id, commit or "no commit", verdict, m.status.value,
        ctl.status(mission_id), _state_proof(ctl, mission_id))

@mcp.tool()
async def mission_validate(mission_id: str, kind: str = "scrutiny", repo: Optional[str] = None, ctx: Optional[Context] = None) -> str:
    """Run a validator now (scrutiny | user_testing | final). Returns a structured verdict."""
    ctl = _controller(repo)
    try:
        return await _call_with_progress(ctl, "validate", ctx, mission_id, kind)
    except Exception as e:
        return _tool_error(ctl, mission_id, e)


@mcp.tool()
def mission_apply_suggestions(mission_id: str, repo: Optional[str] = None) -> str:
    """H10: apply the config deltas recorded at mission create when the model
    preflight flagged the worker model (e.g. worker_timeout>=900 for
    SMALL-OUTPUT-BUDGET models). One call; the applied values are echoed."""
    ctl = _controller(repo)
    try:
        m = ctl.apply_suggestions(mission_id)
    except Exception as e:
        return _tool_error(ctl, mission_id, e)
    return "Suggestions applied.\n\n{}\n\n{}".format(
        ctl.config_summary(mission_id), _state_proof(ctl, mission_id))


@mcp.tool()
def mission_gc(repo: Optional[str] = None, max_age_days: float = 7.0, archive: bool = False) -> str:
    """H11 housekeeping: list terminal and long-stale missions that clutter
    mission_list. With archive=true, non-terminal stale missions older than
    max_age_days are cancelled (their data and event history are kept).
    Terminal missions are never re-touched."""
    ctl = _controller(repo)
    try:
        cands = ctl.gc_candidates(max_age_days=max_age_days)
        archived = []
        if archive:
            for c in cands:
                if c.get("terminal"):
                    continue
                try:
                    ctl.cancel(c["id"])
                    archived.append(c["id"])
                except Exception:
                    continue
    except Exception as e:
        return "TOOL_ERROR: {}: {}".format(type(e).__name__, e)
    import json as _json

    out = "GC candidates: {}\n".format(_json.dumps(cands, indent=2))
    if archive:
        out += "Archived (cancelled): {}\n".format(archived or "none")
    return out


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
        "6. On approval: mission_approve, then drive the mission in SHORT VISIBLE BURSTS: "
        "call mission_run with a small max_steps (e.g. 2-4) and paste the mission control "
        "output (or a 1-2 line summary) between bursts, so the user always sees live "
        "progress instead of a silent wait. If a burst makes no progress and the mission "
        "is not COMPLETED, call mission_events to diagnose why before retrying.\n"
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
        "Check mission_status; if paused/blocked call mission_resume, then drive "
        "mission_run in short visible bursts (small max_steps, paste the mission "
        "control summary between bursts) until it completes or blocks."
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
