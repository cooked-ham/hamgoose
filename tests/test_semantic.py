import json

from hamgoose.config import Config
from hamgoose.models import Feature
from hamgoose.semantic import SemanticClient, extract_text
from hamgoose.worker import GooseRunBackend


def test_inherit_role_values_are_not_passed_as_goose_flags(monkeypatch):
    """The hamgoose `inherit` sentinel must be resolved/omitted for Goose."""
    monkeypatch.delenv("GOOSE_PROVIDER", raising=False)
    monkeypatch.delenv("GOOSE_MODEL", raising=False)
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return (json.dumps({"messages": [{"role": "assistant", "content": [{"type": "text", "text": "OK"}]}]}), "", 0, False)

    monkeypatch.setattr("hamgoose.gosub.run_captured", fake_run)
    result = SemanticClient(Config()).complete("reply OK", role="orchestrator")

    assert result == "OK"
    assert "inherit" not in captured["cmd"]
    assert "--provider" not in captured["cmd"]
    assert "--model" not in captured["cmd"]
    assert "--quiet" in captured["cmd"]


def test_extract_text_handles_goose_banner_before_json():
    payload = json.dumps({"messages": [{"role": "assistant", "content": [{"type": "text", "text": "OK"}]}]})
    assert extract_text("goose is ready\n" + payload) == "OK"
    array_payload = json.dumps([{"role": "assistant", "content": [{"type": "text", "text": "ARRAY OK"}]}])
    assert extract_text("goose is ready\n" + array_payload) == "ARRAY OK"


def test_explicit_role_provider_and_model_are_preserved(monkeypatch):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return (json.dumps({"messages": [{"role": "assistant", "content": [{"type": "text", "text": "OK"}]}]}), "", 0, False)

    monkeypatch.setattr("hamgoose.gosub.run_captured", fake_run)
    cfg = Config(orchestrator={"provider": "custom_airouter", "model": "Qwen3.8"})
    SemanticClient(cfg).complete("reply OK", role="orchestrator")

    assert "--provider" in captured["cmd"]
    assert captured["cmd"][captured["cmd"].index("--provider") + 1] == "custom_airouter"
    assert "--model" in captured["cmd"]
    assert captured["cmd"][captured["cmd"].index("--model") + 1] == "Qwen3.8"


def test_worker_does_not_pass_inherit_sentinel(monkeypatch, tmp_path):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return (json.dumps({"messages": [{"role": "assistant", "content": [{"type": "text", "text": "OK"}]}]}), "", 0, False)

    monkeypatch.setattr("hamgoose.gosub.run_captured", fake_run)
    GooseRunBackend().run("do work", str(tmp_path), Config().resolved_worker(), Feature(id="F001", title="feature"), None)

    assert "inherit" not in captured["cmd"]
    assert "--provider" not in captured["cmd"]
    assert "--model" not in captured["cmd"]
    assert "--quiet" in captured["cmd"]
