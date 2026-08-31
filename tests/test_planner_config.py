"""H2/H3/H10: planner role pinning, unknown-config-key visibility, model-aware
defaults and appliable readiness suggestions."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from harness import make_controller  # noqa: E402

from hamgoose.config import Config  # noqa: E402
from hamgoose.semantic import SemanticClient, SemanticResult  # noqa: E402


def test_planner_pin_is_honored():
    cfg = Config.load({"planner": {"provider": "custom_airouter", "model": "Qwen3.8"}})
    rp = cfg.resolved_planner()
    assert rp["provider"] == "custom_airouter"
    assert rp["model"] == "Qwen3.8"


def test_planner_falls_back_to_orchestrator_then_env(monkeypatch):
    monkeypatch.delenv("GOOSE_PROVIDER", raising=False)
    monkeypatch.delenv("GOOSE_MODEL", raising=False)
    cfg = Config.load({})
    assert cfg.resolved_planner() == cfg.resolved_orchestrator()

    cfg2 = Config.load({"orchestrator": {"provider": "op", "model": "om"}})
    assert cfg2.resolved_planner()["model"] == "om"  # planner inherits orchestrator

    cfg3 = Config.load({
        "orchestrator": {"provider": "op", "model": "om"},
        "planner": {"model": "pm"},
    })
    assert cfg3.resolved_planner()["provider"] == "op"
    assert cfg3.resolved_planner()["model"] == "pm"


def test_unknown_config_keys_are_recorded_not_dropped():
    cfg = Config.load({"planner": {"model": "m"}, "plannner": {"model": "typo"}})
    assert cfg.unrecognized_keys == ["plannner"]
    assert "planner" not in cfg.unrecognized_keys


def test_planner_timeout_flows_through_mission_config():
    cfg = Config.load({"execution": {"planner_timeout": 777}})
    assert cfg.execution.planner_timeout == 777


def test_create_mission_warns_on_unknown_keys(tmp_path):
    ctl = make_controller(tmp_path)
    m = ctl.create_mission("g", config_overrides={"plannr": {"model": "typo"}})
    notes = m.readiness.get("notes") or []
    assert any("plannr" in n and "unknown config keys" in n for n in notes)


def test_config_summary_shows_planner_and_divergence(tmp_path):
    ctl = make_controller(tmp_path, config_over={
        "worker": {"provider": "p1", "model": "m1"},
        "planner": {"provider": "p2", "model": "m2"},
    })
    m = ctl.create_mission("g")
    summary = ctl.config_summary(m.id)
    assert "planner" in summary
    assert "WARNING: roles resolve to different models" in summary
    assert "planner=p2/m2" in summary


def test_defaults_cover_small_output_budget_models():
    """H3/H4: the Qwen3.8-class failures came from 420/180 s caps."""
    cfg = Config()
    assert cfg.execution.worker_timeout == 900
    assert cfg.execution.semantic_timeout == 600
    assert cfg.execution.planner_timeout == 600


def test_preflight_records_suggestions_and_apply_deltas(tmp_path, monkeypatch):
    """H3/H10: a flagged model produces concrete, appliable config deltas."""
    captured = {}

    def fake_smoke(self, prompt, role="worker", timeout=60, max_turns=2):
        captured["config"] = self.config
        return SemanticResult(text="", timed_out=True, duration=60.0)

    monkeypatch.setattr(SemanticClient, "smoke", fake_smoke)
    ctl = make_controller(tmp_path, config_over={
        "execution": {"worker_timeout": 420, "semantic_timeout": 180},
    })
    ctl.worker_backend.name = "goose_run"  # instance attr only: no global pollution
    m = ctl.create_mission("g")
    suggested = (m.repo_analysis or {}).get("suggested_config")
    assert suggested, "flagged model must record suggested budgets"
    assert suggested["execution"]["worker_timeout"] == 900
    assert suggested["execution"]["semantic_timeout"] == 600
    m2 = ctl.apply_suggestions(m.id)
    assert m2.config["execution"]["worker_timeout"] == 900
    assert m2.config["execution"]["semantic_timeout"] == 600
    evs = [e for e in store_events(tmp_path, m.id) if e["type"] == "SUGGESTIONS_APPLIED"]
    assert evs


def store_events(repo, mission_id):
    from hamgoose import store

    return store.read_events(str(repo), mission_id)


def test_apply_suggestions_without_suggestions_raises(tmp_path):
    ctl = make_controller(tmp_path)
    m = ctl.create_mission("g")
    try:
        ctl.apply_suggestions(m.id)
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
