import os

from hamgoose import store
from hamgoose.models import Feature, FeatureStatus, Milestone, Mission, MissionStatus


def _m(repo):
    m = Mission(id="M1", goal="g", repo=repo, status=MissionStatus.RUNNING)
    m.features["F001"] = Feature(id="F001", title="t", milestone="MS01", status=FeatureStatus.COMPLETED)
    m.milestones["MS01"] = Milestone(id="MS01", objective="o", features=["F001"])
    return m


def test_roundtrip(tmp_path):
    m = _m(str(tmp_path))
    store.save_mission(m)
    m2 = store.load_mission(str(tmp_path), "M1")
    assert m2.features["F001"].title == "t"
    assert m2.milestones["MS01"].objective == "o"
    assert m2.status == MissionStatus.RUNNING
    # human-readable mirrors exist
    assert os.path.exists(store.plan_path(str(tmp_path), "M1"))


def test_atomic_no_partial(tmp_path):
    m = _m(str(tmp_path))
    store.save_mission(m)
    for _ in range(5):
        store.save_mission(m)
    assert store.load_mission(str(tmp_path), "M1") is not None


def test_events_append_only(tmp_path):
    m = _m(str(tmp_path))
    store.append_event(m, "MISSION_CREATED", "M1")
    store.append_event(m, "WORKER_STARTED", "F001")
    store.save_mission(m)
    evs = store.read_events(str(tmp_path), "M1")
    assert len(evs) >= 2
    assert evs[0]["type"] == "MISSION_CREATED"
    assert os.path.exists(store.events_path(str(tmp_path), "M1"))
