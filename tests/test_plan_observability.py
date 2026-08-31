"""HG-06: planner observability - separate planner timeout, evidence in
PLAN_FAILED payloads, small-slice retry, and semantic result objects."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from harness import F, MS, make_controller  # noqa: E402

from hamgoose import store  # noqa: E402
from hamgoose.config import Config  # noqa: E402
from hamgoose.controller import MissionController  # noqa: E402
from hamgoose.models import Mission, MissionStatus  # noqa: E402
from hamgoose.semantic import SemanticClient, SemanticResult  # noqa: E402

PLAN_JSON = (
    "```json\n"
    '{"milestones": [{"id": "MS01", "objective": "o"}], '
    '"features": [{"id": "F001", "title": "do the work", "milestone": "MS01", '
    '"acceptance_criteria": ["done"], "expected_paths": ["src"]}]}'
    "\n```"
)


class _RecordingSemantic:
    """Legacy-style semantic client (complete only) with scripted results."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0
        self.prompts = []

    def complete(self, prompt, role=None):
        self.prompts.append(prompt)
        self.calls += 1
        return self.responses[min(self.calls - 1, len(self.responses) - 1)]


class _DetailedSemantic:
    def __init__(self, results):
        self.results = list(results)
        self.calls = 0
        self.timeouts = []
        self.prompts = []

    def complete_detailed(self, prompt, role="orchestrator", timeout=None, max_turns=None):
        self.calls += 1
        self.timeouts.append(timeout)
        self.prompts.append(prompt)
        r = self.results[min(self.calls - 1, len(self.results) - 1)]
        return r if isinstance(r, SemanticResult) else SemanticResult(text=str(r))


def _mission(tmp_path):
    m = Mission(id="M-PLN", goal="g", repo=str(tmp_path), status=MissionStatus.PLANNING)
    store.save_mission(m)
    return m


def test_planner_timeout_is_separate_and_default_600():
    assert Config().execution.planner_timeout == 600
    # H4: 180 s killed validators mid-verdict; the validator/planner budget is
    # now 600 s by default and the two remain independent knobs.
    assert Config().execution.semantic_timeout == 600
    cfg = Config.load({"execution": {"planner_timeout": 1200}})
    assert cfg.execution.planner_timeout == 1200
    assert cfg.execution.semantic_timeout == 600


def test_complete_detailed_honors_explicit_timeout(monkeypatch, tmp_path):
    captured = {}

    def fake_run(cmd, cwd=None, timeout=None, env=None, on_poll=None, poll_interval=5.0):
        captured["timeout"] = timeout
        return ('{"messages": [{"role": "assistant", "content": [{"type": "text", "text": "OK"}]}]}',
                "", 0, False)

    monkeypatch.setattr("hamgoose.gosub.run_captured", fake_run)
    sem = SemanticClient(Config())
    res = sem.complete_detailed("p", role="orchestrator", timeout=600)
    assert isinstance(res, SemanticResult)
    assert res.text == "OK" and not res.timed_out
    assert captured["timeout"] == 600
    # default falls back to semantic_timeout
    sem.complete_detailed("p", role="orchestrator")
    assert captured["timeout"] == 600
    # smoke uses its own bounds
    sem.smoke("p", timeout=60, max_turns=2)
    assert captured["timeout"] == 60


def test_complete_detailed_reports_timeout_and_tail(monkeypatch):
    def fake_run(cmd, **kw):
        return ("partial output...", "warn", None, True)

    monkeypatch.setattr("hamgoose.gosub.run_captured", fake_run)
    res = SemanticClient(Config()).complete_detailed("p", timeout=5)
    assert res.timed_out
    assert "partial output" in res.raw_tail
    assert res.ok is False


def test_plan_failed_payload_carries_evidence(tmp_path):
    sem = _DetailedSemantic([SemanticResult(text="", timed_out=True, raw_tail="cut off mid-repo-analysis")])
    c = MissionController(str(tmp_path), semantic=sem)
    m = _mission(tmp_path)
    try:
        c.plan(m.id)
        raise AssertionError("plan() must fail loudly on empty plan")
    except ValueError:
        pass
    evs = [e for e in store.read_events(str(tmp_path), "M-PLN") if e["type"] == "PLAN_FAILED"]
    assert evs, "a planner death must always produce an event (HG-06)"
    p = evs[-1]["payload"]
    assert p["timed_out"] is True
    assert "cut off mid-repo-analysis" in p["raw_tail"]
    assert p["attempts"] >= 1


def test_small_slice_retry_recovers_after_timeouts(tmp_path):
    sem = _DetailedSemantic([
        SemanticResult(text="", timed_out=True, raw_tail="t1"),
        SemanticResult(text="", timed_out=True, raw_tail="t2"),
        SemanticResult(text=PLAN_JSON),
    ])
    c = MissionController(str(tmp_path), semantic=sem)
    m = _mission(tmp_path)
    c.plan(m.id)
    assert sem.calls == 3
    assert "top-level entries" in sem.prompts[2]  # third prompt used the small slice
    m2 = c._get(m.id)
    assert m2.status == MissionStatus.AWAITING_APPROVAL
    assert "F001" in m2.features


def test_planner_uses_planner_timeout_not_semantic_timeout(tmp_path):
    sem = _DetailedSemantic([SemanticResult(text=PLAN_JSON)])
    c = MissionController(str(tmp_path), semantic=sem)
    m = _mission(tmp_path)
    c.plan(m.id)
    assert sem.timeouts[0] == Config().execution.planner_timeout


def test_every_planner_exit_produces_an_event(tmp_path):
    """No silent death: success AND failure paths both emit events."""
    sem = _RecordingSemantic([PLAN_JSON])
    c = MissionController(str(tmp_path), semantic=sem)
    m = _mission(tmp_path)
    c.plan(m.id)
    types = [e["type"] for e in store.read_events(str(tmp_path), "M-PLN")]
    assert "PLAN_GENERATED" in types

    sem2 = _RecordingSemantic(["garbage"] * 4)
    c2 = MissionController(str(tmp_path), semantic=sem2)
    m2 = Mission(id="M-PLN2", goal="g", repo=str(tmp_path), status=MissionStatus.PLANNING)
    store.save_mission(m2)
    try:
        c2.plan("M-PLN2")
    except ValueError:
        pass
    types2 = [e["type"] for e in store.read_events(str(tmp_path), "M-PLN2")]
    assert "PLAN_FAILED" in types2
