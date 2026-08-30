"""Tests for one-shot config registration (hamgoose register/unregister)."""
import yaml

from hamgoose.register import (
    cli_main,
    find_goose_config_file,
    register,
    resolve_command,
    unregister,
)


def test_register_creates_config(tmp_path):
    cfg = tmp_path / "config.yaml"
    r = register(cfg)
    assert r["status"] == "registered"
    data = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    e = data["extensions"]["hamgoose"]
    assert e["type"] == "stdio"
    assert e["enabled"] is True
    assert e["name"] == "hamgoose"
    assert "hamgoose" in e["command"]
    # no backup needed on first creation
    assert not (tmp_path / "config.yaml.bak").exists()


def test_register_preserves_existing_entries(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "extensions:\n"
        "  notion:\n"
        "    enabled: false\n"
        "    type: stdio\n"
        "    command: notion-mcp\n"
        "providers:\n"
        "  openai:\n"
        "    api_key: x\n",
        encoding="utf-8",
    )
    r = register(cfg)
    assert r["status"] == "registered"
    data = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    assert data["extensions"]["notion"]["command"] == "notion-mcp"
    assert data["providers"]["openai"]["api_key"] == "x"
    assert data["extensions"]["hamgoose"]["type"] == "stdio"
    assert (tmp_path / "config.yaml.bak").exists()


def test_register_idempotent_without_force(tmp_path):
    cfg = tmp_path / "config.yaml"
    register(cfg)
    first = cfg.read_text(encoding="utf-8")
    r = register(cfg)
    assert r["status"] == "already_registered"
    assert cfg.read_text(encoding="utf-8") == first


def test_register_force_overwrites(tmp_path):
    cfg = tmp_path / "config.yaml"
    register(cfg)
    data = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    data["extensions"]["hamgoose"]["enabled"] = False
    cfg.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    r = register(cfg, force=True)
    assert r["status"] == "registered"
    data2 = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    assert data2["extensions"]["hamgoose"]["enabled"] is True


def test_unregister(tmp_path):
    cfg = tmp_path / "config.yaml"
    register(cfg)
    r = unregister(cfg)
    assert r["status"] == "unregistered"
    data = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    assert "hamgoose" not in (data.get("extensions") or {})
    r2 = unregister(cfg)
    assert r2["status"] == "not_registered"


def test_unregister_preserves_other_extensions(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("extensions:\n  keepme:\n    type: stdio\n    command: x\n", encoding="utf-8")
    register(cfg)
    unregister(cfg)
    data = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    assert "keepme" in data["extensions"]
    assert "hamgoose" not in data["extensions"]


def test_env_override(tmp_path, monkeypatch):
    target = tmp_path / "alt.yaml"
    monkeypatch.setenv("HAMGOOSE_CONFIG_FILE", str(target))
    assert find_goose_config_file() == target


def test_resolve_command_prefers_path(monkeypatch):
    monkeypatch.setattr(
        "hamgoose.register.shutil.which",
        lambda name: "C:\\bin\\hamgoose.exe" if name == "hamgoose" else None,
    )
    assert resolve_command() == "hamgoose"


def test_resolve_command_fallback_to_module(monkeypatch):
    monkeypatch.setattr("hamgoose.register.shutil.which", lambda name: None)
    cmd = resolve_command()
    assert cmd.endswith("-m hamgoose")


def test_cli_main_end_to_end(tmp_path, capsys):
    cfg = tmp_path / "config.yaml"
    assert cli_main(["register", "--config", str(cfg)]) == 0
    out = capsys.readouterr().out
    assert "registered" in out
    assert cli_main(["unregister", "--config", str(cfg)]) == 0
    out2 = capsys.readouterr().out
    assert "unregistered" in out2
