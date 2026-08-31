"""HG-08: canonical config channel (env < repo file < overrides) and
CONFIG_DRIFT detection when persisted config is hand-edited."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from harness import F, MS, make_controller  # noqa: E402

from hamgoose import store  # noqa: E402
from hamgoose.config import Config  # noqa: E402


def _write_repo_config(repo, data):
    d = os.path.join(repo, ".goose", "hamgoose")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "config.json"), "w") as f:
        json.dump(data, f)


def test_precedence_env_lt_file_lt_overrides(tmp_path, monkeypatch):
    repo = str(tmp_path)
    monkeypatch.setenv("HAMGOOSE_CONFIG", json.dumps({"execution": {"worker_timeout": 111}}))
    _write_repo_config(repo, {"execution": {"worker_timeout": 222, "max_feature_attempts": 5}})
    # env only
    assert Config.load(repo=None).execution.worker_timeout == 111
    # file beats env
    assert Config.load(repo=repo).execution.worker_timeout == 222
    assert Config.load(repo=repo).execution.max_feature_attempts == 5
    # overrides beat file
    cfg = Config.load({"execution": {"worker_timeout": 333}}, repo=repo)
    assert cfg.execution.worker_timeout == 333
    assert cfg.execution.max_feature_attempts == 5  # untouched keys survive


def test_repo_file_affects_new_missions(tmp_path, monkeypatch):
    repo = str(tmp_path)
    _write_repo_config(repo, {"execution": {"max_feature_attempts": 7}})
    ctl = make_controller(repo)  # controller loads via repo channel
    m = ctl.create_mission("g")
    assert m.config["execution"]["max_feature_attempts"] == 7


def test_malformed_repo_file_is_ignored(tmp_path):
    repo = str(tmp_path)
    d = os.path.join(repo, ".goose", "hamgoose")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "config.json"), "w") as f:
        f.write("{not json")
    assert Config.load(repo=repo).execution.worker_timeout == 420  # factory default


def test_config_drift_event_fires_on_hand_edit(tmp_path):
    repo = str(tmp_path)
    ctl = make_controller(repo)
    m = ctl.create_mission("g")
    ctl.plan(m.id, features=[F("F001", "t")], milestones=[MS("MS01", "o")])

    # simulate a hand edit of the persisted mission.json
    mj = store.mission_json(repo, m.id)
    disk = json.load(open(mj))
    disk["config"]["execution"]["worker_timeout"] = 9999
    with open(mj, "w") as f:
        json.dump(disk, f)

    # next controller save surfaces the drift instead of silently losing it
    m2 = ctl._get(m.id)
    store.save_mission(m2)
    evs = [e for e in store.read_events(repo, m.id) if e["type"] == "CONFIG_DRIFT"]
    assert evs, "hand-edited config must produce a CONFIG_DRIFT event"
    changed = evs[-1]["payload"]["changed"]
    assert any("worker_timeout" in k for k in changed)


def test_yaml_hand_edit_surfaces_drift(tmp_path):
    repo = str(tmp_path)
    ctl = make_controller(repo)
    m = ctl.create_mission("g")
    yp = os.path.join(store.mission_dir(repo, m.id), "mission.yaml")
    text = open(yp).read().replace("worker_timeout: 420", "worker_timeout: 1800")
    with open(yp, "w") as f:
        f.write(text)
    m2 = ctl._get(m.id)
    store.save_mission(m2)
    evs = [e for e in store.read_events(repo, m.id) if e["type"] == "CONFIG_DRIFT"]
    assert any(e["payload"].get("source") == "mission.yaml" for e in evs)


def test_mission_create_echoes_effective_config(tmp_path):
    repo = str(tmp_path)
    ctl = make_controller(repo)
    m = ctl.create_mission("g")
    summary = ctl.config_summary(m.id)
    assert "EFFECTIVE CONFIG" in summary
    assert "inherit" in summary  # resolved values are echoed, including (inherit)
    assert "planner_timeout" in summary
    assert "worker_timeout" in summary
