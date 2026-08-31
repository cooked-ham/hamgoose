"""Guided setup: user rules are a first-class citizen.

They are captured at mission start, persisted on the mission, shown in the
plan/status, fed to every worker prompt, and surfaced through the MCP tools
and the start_mission prompt (the /command walkthrough).
"""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import hamgoose.server as server  # noqa: E402
from hamgoose.models import Feature, Mission, MissionStatus  # noqa: E402
from hamgoose.prompting import worker_prompt  # noqa: E402
from hamgoose.render import mission_control, plan_md  # noqa: E402
from hamgoose.validator import MockValidationBackend  # noqa: E402
from hamgoose.worker import MockBackend  # noqa: E402

RULES = "My provider allows max 3 concurrent agents; never exceed that."

_ORIG = server._controller


def _ctl(repo=None, config=None):
    c = _ORIG(repo, server._parse_config(config))
    c.worker_backend = MockBackend()
    c.validation_backend = MockValidationBackend()
    c.planner = lambda mission, goal: {
        "milestones": [{"id": "MS01", "objective": "foundation"}],
        "features": [
            {
                "id": "F001", "title": "Build foundation", "description": "d",
                "milestone": "MS01", "dependencies": [],
                "acceptance_criteria": ["foundation exists"], "expected_paths": ["app"],
            }
        ],
    }
    return c


@pytest.fixture
def mcp_surface(monkeypatch):
    monkeypatch.setattr(server, "_controller", _ctl)
    yield server.mcp


class _FakeSemantic:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def complete(self, prompt, role=None):
        self.calls += 1
        return self.responses[min(self.calls - 1, len(self.responses) - 1)]


def test_empty_plan_retries_then_fails_loudly(tmp_path):
    """A 0-feature plan must never reach approval: the planner gets one grounded
    retry, then plan() raises and the mission stays re-plannable."""
    from hamgoose import store
    from hamgoose.controller import MissionController

    sem = _FakeSemantic(["no json here", "still no json"])
    c = MissionController(str(tmp_path), server._parse_config(None), semantic=sem)
    m = Mission(id="M-empty", goal="finish the earlier work", repo=str(tmp_path),
                status=MissionStatus.PLANNING)
    store.save_mission(m)
    with pytest.raises(ValueError, match="empty plan"):
        c.plan(m.id)
    assert sem.calls == 2  # first attempt + grounded retry
    m2 = c._get(m.id)
    assert m2.status == MissionStatus.PLANNING  # still re-plannable
    # defense in depth: approve refuses an empty plan even if forced into the state
    m2.status = MissionStatus.AWAITING_APPROVAL
    store.save_mission(m2)
    with pytest.raises(ValueError, match="empty plan"):
        c.approve(m.id)


def _mission():
    return Mission(id="M-test", goal="g", repo=".", rules=RULES, status=MissionStatus.PLANNING)


def test_rules_roundtrip():
    d = _mission().to_dict()
    assert d["rules"] == RULES
    assert Mission.from_dict(d).rules == RULES
    no_rules = {k: v for k, v in d.items() if k != "rules"}
    assert Mission.from_dict(no_rules).rules is None  # back-compat with old files


def test_rules_rendered_in_plan_and_status():
    m = _mission()
    assert RULES in plan_md(m)
    assert RULES in mission_control(m)


def test_worker_prompt_includes_rules():
    f = Feature.from_dict({"id": "F001", "title": "t", "milestone": "MS01"})
    p = worker_prompt(_mission(), f, {}, "")
    assert RULES in p
    assert "USER CONSTRAINTS" in p


def _text(res):
    if isinstance(res, tuple):
        contents, meta = res
        if contents:
            return contents[0].text
        return str(meta)
    return res[0].text


async def test_create_with_rules_surfaces_in_tools(mcp_surface):
    tmp = tempfile.mkdtemp(prefix="hamgoose_rules_")
    mid = _text(await mcp_surface.call_tool("mission_create", {"goal": "g", "repo": tmp, "rules": RULES}))
    mid = mid.split("Mission created:")[1].split("\n")[0].strip()
    assert RULES in _text(await mcp_surface.call_tool("mission_status", {"mission_id": mid, "repo": tmp}))
    assert RULES in _text(await mcp_surface.call_tool("mission_plan_view", {"mission_id": mid, "repo": tmp}))
    # rules survive a reload from disk (stateless extension)
    from hamgoose import store

    assert store.load_mission(tmp, mid).rules == RULES


async def test_start_mission_prompt_is_a_walkthrough(mcp_surface):
    got = await mcp_surface.get_prompt("start_mission", {"goal": "migrate X to Y", "rules": RULES})
    text = ""
    for m in (getattr(got, "messages", None) or [got]):
        c = getattr(m, "content", "")
        if isinstance(c, str):
            text += c
        elif isinstance(c, list):
            text += " ".join(str(getattr(x, "text", x)) for x in c)
        else:
            text += str(c)
    assert "migrate X to Y" in text
    assert RULES in text
    for step in ("mission_create", "mission_plan", "mission_approve", "mission_run"):
        assert step in text
