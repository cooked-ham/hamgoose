"""HG-09: manual retries count toward the attempt budget and the events tell
the truth about it."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from harness import F, MS, make_controller  # noqa: E402

from hamgoose import scheduler, store  # noqa: E402
from hamgoose.models import Feature, FeatureStatus, Milestone, Mission  # noqa: E402


def _mission_with(feat):
    m = Mission(id="M1", goal="g", repo=".")
    m.milestones["MS01"] = Milestone(id="MS01", objective="o", features=[feat.id])
    m.features[feat.id] = feat
    return m


def test_scheduler_budget_counts_manual_retries():
    f = Feature(id="F001", title="t", status=FeatureStatus.FAILED,
                attempts=2, manual_retries=0, max_attempts=3)
    m = _mission_with(f)
    assert [x.id for x in scheduler.ready_features(m, "MS01")] == ["F001"]  # 2 < 3
    f.manual_retries = 1
    assert scheduler.ready_features(m, "MS01") == []  # 2+1 >= 3: exhausted (HG-09)


def test_manual_retry_records_beyond_budget(tmp_path):
    repo = str(tmp_path)
    ctl = make_controller(repo, config_over={"execution": {"max_feature_attempts": 1}})
    m = ctl.create_mission("g")
    ctl.plan(m.id, features=[F("F001", "t")], milestones=[MS("MS01", "o")])
    ctl.approve(m.id)
    # worker claims failure -> attempts 1 >= max 1 -> FAILED
    ctl.worker_backend.simulator = lambda f, wd: {"status": "failed", "summary": "nope",
                                                  "changed_files": [], "notes": []}
    ctl.run(m.id)
    m = ctl._get(m.id)
    f = m.features["F001"]
    assert f.status == FeatureStatus.FAILED

    m = ctl.retry_feature(m.id, "F001")
    f = m.features["F001"]
    assert f.manual_retries == 1
    evs = [e for e in store.read_events(repo, m.id) if e["type"] == "FEATURE_RETRIED"]
    assert evs and evs[-1]["payload"]["manual"] is True
    assert evs[-1]["payload"]["beyond_budget"] is True  # 1+1 >= 1

    # the scheduler will not reschedule it (budget exhausted either way)
    assert scheduler.ready_features(m, "MS01") == []


def test_automated_retries_stop_at_cap(tmp_path):
    repo = str(tmp_path)
    ctl = make_controller(repo, config_over={"execution": {"max_feature_attempts": 2}})
    m = ctl.create_mission("g")
    ctl.plan(m.id, features=[F("F001", "t")], milestones=[MS("MS01", "o")])
    ctl.approve(m.id)
    ctl.worker_backend.simulator = lambda f, wd: {"status": "failed", "summary": "nope",
                                                  "changed_files": [], "notes": []}
    ctl.run(m.id, max_steps=10)
    m = ctl._get(m.id)
    f = m.features["F001"]
    assert f.attempts == 2  # capped
    assert f.status == FeatureStatus.FAILED
    evs = [e for e in store.read_events(repo, m.id) if e["type"] == "FEATURE_FAILED"]
    assert evs and evs[-1]["payload"]["manual_retries"] == 0
