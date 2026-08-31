"""H1: the MCP tool layer must never swallow errors and never return an empty
payload. Every mutating tool returns a TOOL_ERROR string on failure (with a
fresh STATE proof) and a STATE proof line on success."""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from harness import F, MS, create_and_plan, make_controller  # noqa: E402

from hamgoose import server, store  # noqa: E402


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) if False else asyncio.run(coro)


def test_mission_plan_error_returns_tool_error_not_empty(tmp_path):
    """H1 #3 regression: a payload that raises ValueError must reach the client
    as an error, not as an empty success."""
    out = _run(server.mission_plan("M-DOES-NOT-EXIST", repo=str(tmp_path)))
    assert isinstance(out, str) and out.strip(), "tool must never return an empty payload"
    assert out.startswith("TOOL_ERROR:")
    assert "ValueError" in out
    assert "STATE:" in out  # state proof even on the error path
    # the failure is also visible in the event log when the mission exists
    # (here it does not, so no event - but the caller still got the error)


def test_mission_complete_feature_error_surfaces(tmp_path):
    repo = str(tmp_path)
    ctl = make_controller(repo)
    m = create_and_plan(ctl, "g", [F("F001", "t")], [MS("MS01", "o")])
    out = server.mission_complete_feature(m.id, "F-NOPE", summary="x", repo=repo)
    assert out.startswith("TOOL_ERROR:")
    assert "STATE:" in out


def test_plan_with_bad_features_json_is_an_error(tmp_path):
    repo = str(tmp_path)
    ctl = make_controller(repo)
    m = create_and_plan(ctl, "g", [F("F001", "t")], [MS("MS01", "o")])
    out = _run(server.mission_plan(m.id, repo=repo, features="{not json"))
    assert out.startswith("TOOL_ERROR:")
    assert "features" in out


def test_plan_accepts_native_lists(tmp_path):
    """H1: bridge type quirks (native list instead of JSON string) must work."""
    repo = str(tmp_path)
    ctl = make_controller(repo)
    m = create_and_plan(ctl, "g", [F("F001", "t")], [MS("MS01", "o")])
    # drop back to PLANNING-shaped state by planning a fresh mission
    m2 = ctl.create_mission("g2")
    out = _run(server.mission_plan(
        m2.id, repo=repo,
        features=[{"id": "F001", "title": "t", "milestone": "MS01"}],
        milestones=[{"id": "MS01", "objective": "o"}]))
    assert "PLAN" in out
    assert "status AWAITING_APPROVAL" in out
    proof = [l for l in out.splitlines() if l.startswith("STATE:")]
    assert proof and "last_event=PLAN_GENERATED" in proof[0]


def test_state_proof_reflects_disk_state(tmp_path):
    repo = str(tmp_path)
    ctl = make_controller(repo)
    m = create_and_plan(ctl, "g", [F("F001", "t")], [MS("MS01", "o")])
    proof = server._state_proof(ctl, m.id)
    assert proof.startswith("STATE:")
    assert "status=AWAITING_APPROVAL" in proof
    assert "features=0/1" in proof
    assert "last_event=PLAN_GENERATED" in proof


def test_tool_error_appends_event_when_mission_exists(tmp_path):
    repo = str(tmp_path)
    ctl = make_controller(repo)
    m = create_and_plan(ctl, "g", [F("F001", "t")], [MS("MS01", "o")])
    out = server.mission_complete_feature(m.id, "F-NOPE", summary="x", repo=repo)
    assert out.startswith("TOOL_ERROR:")
    evs = [e["type"] for e in store.read_events(repo, m.id)]
    assert "MISSION_TOOL_ERROR" in evs


def test_new_surface_tools_registered():
    from hamgoose.server import get_extension

    get_extension()  # builds without error
