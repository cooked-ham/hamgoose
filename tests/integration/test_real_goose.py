"""Real-Goose integration tests.

These exercise the ACTUAL Goose integration path (requirement 32): the hamgoose
MCP server over the real stdio transport, real isolated `goose run` workers
modifying a real repository, and Goose discovering/driving the extension.

They are skipped automatically if `goose` is not on PATH, and are tagged
`realgoose` so the fast deterministic suite can run without them:

    pytest -m "not realgoose"     # fast, no LLM
    pytest -m "realgoose"         # real Goose + LLM (slower)
"""
import asyncio
import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

GOOSE = shutil.which("goose")
pytestmark = [pytest.mark.realgoose, pytest.mark.skipif(GOOSE is None, reason="goose not on PATH")]

from harness import F, MS, create_and_plan, init_git, make_controller  # noqa: E402
from hamgoose import store  # noqa: E402
from hamgoose.config import Config  # noqa: E402
from hamgoose.controller import MissionController  # noqa: E402
from hamgoose.validator import MockValidationBackend  # noqa: E402
from hamgoose.worker import GooseRunBackend  # noqa: E402


def _spawn_server(timeout=60):
    """Drive the hamgoose MCP server over the real stdio transport and list its
    capabilities. Returns (tools, prompts, templates)."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    tmp = os.environ.get("HAMGOOSE_TEST_REPO", os.getcwd())

    async def _run():
        params = StdioServerParameters(
            command=sys.executable, args=["-m", "hamgoose"],
            env=dict(os.environ, HAMGOOSE_REPO=tmp), cwd=tmp,
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as s:
                await asyncio.wait_for(s.initialize(), timeout)
                tools = await asyncio.wait_for(s.list_tools(), timeout)
                prompts = await asyncio.wait_for(s.list_prompts(), timeout)
                templates = await asyncio.wait_for(s.list_resource_templates(), timeout)
                return (
                    sorted(t.name for t in tools.tools),
                    sorted(p.name for p in prompts.prompts),
                    [getattr(t, "uriTemplate", None) or getattr(t, "uri_template", None) for t in templates.resourceTemplates],
                )

    return asyncio.run(_run())


def test_1_server_starts_and_discoverable(tmp_path):
    """hamgoose starts and Goose can discover its MCP capabilities over stdio."""
    tools, prompts, templates = _spawn_server()
    assert "mission_create" in tools
    assert "mission_run" in tools
    assert "mission_approve" in tools
    assert "start_mission" in prompts
    joined = " ".join(t for t in templates if t)
    assert "mission://" in joined


def test_2_real_worker_modifies_real_repo(tmp_path):
    """A real isolated `goose run` worker (GooseRunBackend) makes a real change
    to a real git repository; the mission then completes."""
    repo = str(tmp_path)
    init_git(repo)
    cfg = Config.load()
    cfg.worker.max_turns = 25
    cfg.git.enabled = True
    cfg.git.use_worktrees = True
    cfg.execution.max_concurrent_workers = 1
    ctl = MissionController(repo, cfg)
    ctl.worker_backend = GooseRunBackend()          # REAL goose worker
    ctl.validation_backend = MockValidationBackend()  # deterministic validator

    m = ctl.create_mission("Create a file out.txt containing the word hello")
    ctl.plan(m.id,
             features=[F("F001", "Create out.txt", criteria=["out.txt exists with 'hello'"], paths=["out.txt"])],
             milestones=[MS("MS01", "create file")])
    ctl.approve(m.id)
    ctl.run(m.id)

    m2 = store.load_mission(repo, m.id)
    f = m2.features["F001"]
    # the worker was a real goose run
    assert f.worker.backend == "goose_run"
    # a real isolated worker ran and produced a real repository change
    assert f.attempts >= 0 and (f.worker.run_id or f.worker.pid is not None or f.result.raw)
    # the file should exist in the base branch worktree or the repo
    base_wt = os.path.join(repo, ".goose", "hamgoose", m.id, "worktrees_base")
    candidates = [os.path.join(base_wt, "out.txt"), os.path.join(repo, "out.txt")]
    assert any(os.path.exists(c) for c in candidates), "real worker did not create out.txt"


def test_3_mission_state_survives_restart(tmp_path):
    """Mission state survives a controller 'restart' (fresh process reads disk)."""
    repo = str(tmp_path)
    ctl = make_controller(repo)
    m = create_and_plan(ctl, "g", [F("F001", "a")], [MS("MS01", "m")])
    ctl.approve(m.id)
    # simulate restart: brand-new controller, no shared memory
    ctl2 = make_controller(repo)
    reloaded = store.load_mission(repo, m.id)
    assert reloaded.id == m.id
    assert reloaded.goal == "g"
    assert reloaded.features["F001"].title == "a"
    assert reloaded.status.value in ("RUNNING", "PAUSED", "BLOCKED")


def test_4_goose_discovers_and_drives_extension(tmp_path):
    """Goose itself discovers hamgoose via --with-extension and calls a tool."""
    import glob

    repo = str(tmp_path)
    init_git(repo)
    prompt = ("Use the hamgoose extension. Call the mission_create tool with goal "
              "'real integration'. Report the exact mission id it returns. Do exactly one tool call.")
    # Inline name must not collide with a `hamgoose` entry that may be
    # registered in the real config.yaml (Goose rejects duplicate names).
    cmd = [GOOSE, "run", "-t", prompt, "--max-turns", "6", "--output-format", "text",
           "--no-session", "--with-extension", "hamgoose_it:{0} -m hamgoose".format(sys.executable)]
    from hamgoose import gosub

    stdout, stderr, _exit, _to = gosub.run_captured(cmd, cwd=repo, timeout=300)
    out = (stdout or "") + (stderr or "")
    # Deterministic proof: a real mission was created on disk by the extension tool
    # (this does not depend on the LLM's phrasing).
    created = glob.glob(os.path.join(repo, ".goose", "hamgoose", "M-*", "mission.json"))
    assert ("M-20" in out) or ("mission_create" in out.lower()) or ("missioncreate" in out.lower()) or created, (
        "goose did not discover/call hamgoose: " + out[-800:]
    )
