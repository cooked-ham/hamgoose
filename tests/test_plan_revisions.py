"""HG-10: plan-revision bookkeeping - 0-feature revisions are impossible, and
external structural changes get recorded instead of silently diverging."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from harness import F, MS, make_controller  # noqa: E402

from hamgoose import store  # noqa: E402
from hamgoose.controller import MissionController  # noqa: E402
from hamgoose.models import Feature, Mission, MissionStatus  # noqa: E402


def test_plan_can_never_persist_zero_feature_revision(tmp_path):
    """Regression for M-2026-1909541C: a 0-feature revision reached
    AWAITING_APPROVAL via a stale pre-v0.1.6 install."""
    repo = str(tmp_path)
    ctl = make_controller(repo)
    ctl.planner = lambda m, goal: {"milestones": [], "features": []}
    m = ctl.create_mission("g")
    try:
        ctl.plan(m.id)
        raise AssertionError("empty plan must raise")
    except ValueError:
        pass
    m2 = ctl._get(m.id)
    assert m2.plan_revisions == []
    assert m2.current_revision == 1
    assert m2.status == MissionStatus.PLANNING


def test_apply_plan_refuses_zero_features(tmp_path):
    repo = str(tmp_path)
    ctl = make_controller(repo)
    m = ctl.create_mission("g")
    try:
        ctl._apply_plan(m, {"milestones": [], "features": []}, note="x", revision=1)
        raise AssertionError("_apply_plan must refuse a 0-feature plan")
    except ValueError:
        pass


def test_external_structural_change_gets_a_revision(tmp_path):
    repo = str(tmp_path)
    ctl = make_controller(repo)
    m = ctl.create_mission("g")
    ctl.plan(m.id, features=[F("F001", "t")], milestones=[MS("MS01", "o")])
    n_revisions = len(ctl._get(m.id).plan_revisions)

    # direct store write adding a feature, bypassing the controller
    m2 = store.load_mission(repo, m.id)
    m2.features["F009"] = Feature(id="F009", title="externally injected", milestone="MS01")
    m2.milestones["MS01"].features.append("F009")
    store.save_mission(m2)

    m3 = store.load_mission(repo, m.id)
    assert len(m3.plan_revisions) == n_revisions + 1
    rev = m3.plan_revisions[-1]
    assert rev.note == "external plan change (store)"
    assert "F009" in rev.feature_ids
    evs = [e for e in store.read_events(repo, m.id) if e["type"] == "PLAN_REVISION_RECORDED"]
    assert evs


def test_controller_structural_change_covers_itself(tmp_path):
    """Controller-driven structural changes (fix features) record their own
    revision so 'external plan change' always means an outside writer."""
    repo = str(tmp_path)
    ctl = make_controller(repo)
    m = ctl.create_mission("g")
    ctl.plan(m.id, features=[F("F001", "t")], milestones=[MS("MS01", "o")])
    m2 = ctl._get(m.id)

    from hamgoose.models import Finding, ValidationResult

    res = ValidationResult(kind="scrutiny", passed=False, severity="major",
                           summary="defect",
                           findings=[Finding(feature="F001", criterion="c", problem="broken",
                                             evidence="e", recommended_fix="fix it")])
    ctl._create_fix_features(m2, m2.milestones["MS01"], [res])
    n_ext = [r for r in m2.plan_revisions if r.note.startswith("external")]
    assert not n_ext  # recorded by the controller, not flagged external
    assert m2.plan_revisions[-1].note.startswith("correction fixes")
