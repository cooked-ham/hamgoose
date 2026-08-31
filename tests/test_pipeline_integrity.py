"""H5/H6/H8/H11: unified config precedence, run reporting, event visibility
and mission housekeeping."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from harness import F, MS, create_and_plan, make_controller  # noqa: E402

from hamgoose import store  # noqa: E402
from hamgoose.config import Config  # noqa: E402
from hamgoose.controller import MissionController  # noqa: E402
from hamgoose.models import Mission, MissionStatus  # noqa: E402
from hamgoose.semantic import SemanticClient, SemanticResult  # noqa: E402
from hamgoose.validator import GooseRunValidationBackend  # noqa: E402


# --------------------------------------------------------------------------- #
# H5: one precedence rule for every semantic role
# --------------------------------------------------------------------------- #
def test_semantic_for_resolves_mission_config(tmp_path):
    """A mission-level override must reach the validator/planner semantic
    client, not just the worker dispatch (the 0.1.8 bug: editing mission.json
    changed workers but never validation)."""
    ctl = make_controller(tmp_path, config_over={"execution": {"semantic_timeout": 4242}})
    m = create_and_plan(ctl, "g", [F("F001", "t")], [MS("MS01", "o")])
    sem = ctl._semantic_for(m)
    assert sem.config.execution.semantic_timeout == 4242
    # worker dispatch uses the same channel
    assert ctl._cfg(m).execution.semantic_timeout == 4242


def test_injected_semantic_still_wins(tmp_path):
    sem = SemanticClient(Config())
    ctl = MissionController(str(tmp_path), semantic=sem)
    m = ctl.create_mission("g")
    assert ctl._semantic_for(m) is sem


class _CaptureSemantic(SemanticClient):
    def __init__(self, config, captured):
        super().__init__(config)
        self.captured = captured

    def complete_detailed(self, prompt, role="orchestrator", timeout=None, max_turns=None):
        self.captured.setdefault("role", []).append(role)
        self.captured.setdefault("timeout", []).append(timeout)
        return SemanticResult(text="ok")


def test_validator_backend_resolves_timeout_from_mission_config(tmp_path):
    captured = {}

    def factory(mission):
        return _CaptureSemantic(Config.load(mission.config), captured)

    backend = GooseRunValidationBackend(factory)
    m = Mission(id="M-V", goal="g", repo=str(tmp_path),
                config=Config.load({"execution": {"semantic_timeout": 555}}).to_dict())
    r = backend.run("scrutiny", m, "", "a", "b", str(tmp_path), "")
    assert not r.timed_out
    assert captured["timeout"] == [555]
    assert captured["role"] == ["validator"]


def test_validator_backend_doubles_on_infra_retries(tmp_path):
    captured = {}

    def factory(mission):
        return _CaptureSemantic(Config.load(mission.config), captured)

    backend = GooseRunValidationBackend(factory)
    m = Mission(id="M-V2", goal="g", repo=str(tmp_path),
                config=Config.load({"execution": {"semantic_timeout": 100}}).to_dict())
    m.milestones["MS01"] = __import__("hamgoose.models", fromlist=["Milestone"]).Milestone(
        id="MS01", objective="o")
    m.milestones["MS01"].validation_infra_retries = 2
    backend.run("scrutiny", m, "MS01", "a", "b", str(tmp_path), "")
    assert captured["timeout"] == [300]


# --------------------------------------------------------------------------- #
# H6: mission_run tells the caller what one call did
# --------------------------------------------------------------------------- #
def test_run_report_includes_dispatches_and_queue(tmp_path):
    ctl = make_controller(tmp_path)
    m = create_and_plan(ctl, "g",
                        [F("F001", "t"), F("F002", "u", deps=["F001"])],
                        [MS("MS01", "o")])
    ctl.approve(m.id)
    m = ctl._get(m.id)
    out = ctl.run(m.id, max_steps=1)  # one step = one dispatch
    assert "RUN REPORT" in out
    assert "dispatches this call=1" in out
    assert "ready/queued now=1" in out
    assert "max_steps counts dispatches" in out
    # the whole remaining work can finish in later calls
    out = ctl.run(m.id)
    assert m.status.value in ("RUNNING", "COMPLETED")
    assert "dispatches this call=1" in out

# --------------------------------------------------------------------------- #
# H8: events are immediately visible, and status shows last-event age
# --------------------------------------------------------------------------- #
def test_append_event_is_visible_to_a_fresh_reader(tmp_path):
    ctl = make_controller(tmp_path)
    m = ctl.create_mission("g")
    store.append_event(m, "TEST_EVENT", entity="x")
    evs = store.read_events(str(tmp_path), m.id)
    assert evs[-1]["type"] == "TEST_EVENT"


def test_status_shows_last_event_age(tmp_path):
    from hamgoose.render import mission_control

    ctl = make_controller(tmp_path)
    m = ctl.create_mission("g")
    m2 = ctl._get(m.id)
    text = mission_control(m2)
    assert "Last event:" in text
    assert "s ago" in text


# --------------------------------------------------------------------------- #
# H11: stale missions are surfaced and archivable
# --------------------------------------------------------------------------- #
def test_list_missions_flags_stale_and_terminal(tmp_path):
    ctl = make_controller(tmp_path)
    m = ctl.create_mission("g")
    ctl2 = MissionController(str(tmp_path), Config(git={"enabled": False}))
    listing = {x["id"]: x for x in ctl2.list()}
    entry = listing[m.id]
    assert entry["terminal"] is False
    assert entry["age_days"] is not None
    assert entry["stale"] is False  # fresh, non-terminal

    ctl2.cancel(m.id)
    listing = {x["id"]: x for x in ctl2.list()}
    assert listing[m.id]["terminal"] is True
    assert listing[m.id]["stale"] is True  # terminal missions are always stale


def test_gc_candidates_and_archive(tmp_path):
    ctl = make_controller(tmp_path)
    m1 = ctl.create_mission("keep me fresh")
    m2 = ctl.create_mission("cancel me")
    ctl.cancel(m2.id)

    cands = ctl.gc_candidates(max_age_days=7.0)
    ids = {c["id"] for c in cands}
    assert m2.id in ids      # terminal
    assert m1.id not in ids  # fresh and active

    # aging m1 beyond the threshold makes it a candidate; archive cancels it
    from datetime import datetime, timezone

    mj = store.mission_json(str(tmp_path), m1.id)
    import json as _json

    disk = _json.load(open(mj))
    disk["updated_at"] = "2026-01-01T00:00:00+00:00"
    with open(mj, "w") as fh:
        _json.dump(disk, fh)

    cands = ctl.gc_candidates(max_age_days=7.0)
    assert {c["id"] for c in cands} == {m1.id, m2.id}
    archived = [c for c in ctl.gc_candidates(max_age_days=7.0) if not c["terminal"]]
    assert [c["id"] for c in archived] == [m1.id]
    ctl.cancel(m1.id)
    assert ctl._get(m1.id).status == MissionStatus.CANCELLED
