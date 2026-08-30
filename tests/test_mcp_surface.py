"""In-process MCP surface tests: exercise the mission_* TOOLS through FastMCP's
async API with deterministic backends, proving the tool layer (schemas, wiring)
works end to end, independent of the controller-level integration tests.

Direct `FastMCP` API return shapes (mcp 1.x):
- list_tools/list_prompts/list_resource_templates -> list of objects
- call_tool(name, args) -> (list[Content], dict) ; structured data under dict["result"]
"""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import hamgoose.server as server  # noqa: E402
from hamgoose.validator import MockValidationBackend  # noqa: E402
from hamgoose.worker import MockBackend  # noqa: E402

PLAN_MS = [{"id": "MS01", "objective": "foundation"}]
PLAN_FEATURES = [
    {"id": "F001", "title": "Build foundation", "description": "d", "milestone": "MS01",
     "dependencies": [], "acceptance_criteria": ["foundation exists"], "expected_paths": ["app"]},
]

_ORIG = server._controller


def _ctl(repo=None, config=None):
    c = _ORIG(repo, server._parse_config(config))
    c.worker_backend = MockBackend()
    c.validation_backend = MockValidationBackend()
    c.planner = lambda mission, goal: {"milestones": PLAN_MS, "features": PLAN_FEATURES}
    return c


def _text(res):
    if isinstance(res, tuple):
        contents, meta = res
        if contents:
            return contents[0].text
        return str(meta)
    if isinstance(res, list):
        return res[0].text
    return str(res)


@pytest.fixture
def mcp_surface(monkeypatch):
    monkeypatch.setattr(server, "_controller", _ctl)
    yield server.mcp


async def test_tool_surface(mcp_surface):
    tools = await mcp_surface.list_tools()
    names = [t.name for t in tools]
    for expected in ("mission_create", "mission_plan", "mission_approve", "mission_run",
                     "mission_pause", "mission_resume", "mission_steer", "mission_replan",
                     "mission_cancel", "mission_retry_feature", "mission_validate",
                     "mission_status", "mission_list"):
        assert expected in names


async def test_prompt_and_resource_surface(mcp_surface):
    prompts = await mcp_surface.list_prompts()
    pnames = [p.name for p in prompts]
    assert "start_mission" in pnames
    templates = await mcp_surface.list_resource_templates()
    joined = " ".join((getattr(t, "uriTemplate", None) or "") for t in templates)
    assert "mission://" in joined
    assert "/validation" in joined


async def test_full_mission_via_tools(mcp_surface):
    tmp = tempfile.mkdtemp(prefix="hamgoose_mcp_")
    t = _text(await mcp_surface.call_tool("mission_create", {"goal": "g", "repo": tmp}))
    mid = t.split("Mission created:")[1].split("\n")[0].strip()
    assert "AWAITING_APPROVAL" in _text(await mcp_surface.call_tool("mission_plan", {"mission_id": mid, "repo": tmp}))
    assert "RUNNING" in _text(await mcp_surface.call_tool("mission_approve", {"mission_id": mid, "repo": tmp}))
    run_t = _text(await mcp_surface.call_tool("mission_run", {"mission_id": mid, "repo": tmp}))
    assert "COMPLETED" in run_t
    assert "COMPLETED" in _text(await mcp_surface.call_tool("mission_status", {"mission_id": mid, "repo": tmp}))
    lst = await mcp_surface.call_tool("mission_list", {"repo": tmp})
    items = (lst[1].get("result") or []) if isinstance(lst, tuple) else (lst or [])
    assert any(m.get("id") == mid for m in items)
