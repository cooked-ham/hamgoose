"""HG-07 / HG-16: model-capability preflight and version stamping."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest

import hamgoose  # noqa: E402
from harness import make_controller  # noqa: E402

from hamgoose import store  # noqa: E402
from hamgoose.controller import MissionController  # noqa: E402
from hamgoose.render import mission_control, readiness_md  # noqa: E402
from hamgoose.semantic import SemanticClient, SemanticResult  # noqa: E402
from hamgoose.worker import GooseRunBackend  # noqa: E402

SMOKE_OK = '```json\n{"ok": true, "note": "structured"}\n```'


def _ctl_with_goose_backend(tmp_path, monkeypatch, smoke_result):
    monkeypatch.setattr(SemanticClient, "smoke",
                        lambda self, prompt, **kw: smoke_result)
    ctl = MissionController(str(tmp_path))  # default backend = GooseRunBackend
    assert ctl.worker_backend.name == "goose_run"
    return ctl


def test_preflight_ok_records_model_check(tmp_path, monkeypatch):
    ctl = _ctl_with_goose_backend(tmp_path, monkeypatch,
                                  SemanticResult(text=SMOKE_OK, duration=3.2))
    m = ctl.create_mission("g")
    mc = m.repo_analysis["model_check"]
    assert mc["ok"] is True
    assert mc["verdict"] == "smoke OK"
    assert mc["duration"] == 3.2
    assert m.readiness.get("Worker model") == "PASS"
    assert any("Worker model" in n and "smoke OK" in n for n in m.readiness.get("notes", []))
    # readiness render + status show the line before approval (HG-07)
    assert "Worker model" in readiness_md(m.readiness)
    assert "Worker model" in mission_control(m)


def test_preflight_timeout_flips_to_warn(tmp_path, monkeypatch):
    ctl = _ctl_with_goose_backend(tmp_path, monkeypatch,
                                  SemanticResult(text="", timed_out=True,
                                                 raw_tail="killed at 60s"))
    m = ctl.create_mission("g")
    mc = m.repo_analysis["model_check"]
    assert mc["ok"] is False
    assert mc["verdict"] == "SMALL-OUTPUT-BUDGET"
    assert m.readiness.get("Worker model") == "WARN"


def test_preflight_limit_evidence_detected(tmp_path, monkeypatch):
    ctl = _ctl_with_goose_backend(
        tmp_path, monkeypatch,
        SemanticResult(text="", raw_tail='{"outputTokenLimitReached": true, "output_tokens": 210}'))
    m = ctl.create_mission("g")
    mc = m.repo_analysis["model_check"]
    assert mc["ok"] is False
    assert mc["output_tokens"] == 210
    assert "outputTokenLimitReached" in mc["limit_evidence"]


def test_preflight_never_fails_creation(tmp_path, monkeypatch):
    def boom(self, prompt, **kw):
        raise RuntimeError("goose exploded")

    monkeypatch.setattr(SemanticClient, "smoke", boom)
    ctl = MissionController(str(tmp_path))
    m = ctl.create_mission("g")  # must not raise
    assert m.repo_analysis["model_check"]["ok"] is False


def test_preflight_skipped_for_mock_backend(tmp_path):
    ctl = make_controller(tmp_path)  # MockBackend
    m = ctl.create_mission("g")
    assert "model_check" not in m.repo_analysis


def test_preflight_disabled_by_config(tmp_path, monkeypatch):
    from hamgoose.config import Config

    monkeypatch.setattr(SemanticClient, "smoke",
                        lambda self, prompt, **kw: pytest.fail("must not run"))
    ctl = MissionController(str(tmp_path), Config(execution={"model_preflight": False}))
    m = ctl.create_mission("g")
    assert "model_check" not in m.repo_analysis


def test_version_stamp_everywhere(tmp_path):
    """HG-16: the extension version is visible on the first event, in
    readiness and in status, so stale installs are detectable."""
    ctl = make_controller(tmp_path)
    m = ctl.create_mission("g")
    assert hamgoose.__version__ and hamgoose.__version__ != "unknown"
    evs = [e for e in store.read_events(str(tmp_path), m.id) if e["type"] == "MISSION_CREATED"]
    assert evs[0]["payload"]["hamgoose_version"] == hamgoose.__version__
    assert m.readiness.get("hamgoose_version") == hamgoose.__version__
    assert "hamgoose version" in readiness_md(m.readiness)
