"""Integration tests for the full orchestration path (scenarios A-L).

These run the real MissionController with deterministic mock backends so the
entire lifecycle (scheduling, dependencies, validation loop, corrective work,
pause/resume, crash recovery, steering, replanning, git conflicts, final gate)
is exercised without a live model. A separate test suite covers the real Goose
integration path.
"""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: F401

from harness import F, MS, create_and_plan, make_controller
from hamgoose import store
from hamgoose.models import FeatureStatus, MissionStatus


def _ok(feature, workdir):
    return {"status": "completed", "summary": "ok", "changed_files": [feature.id + ".done"], "notes": []}


# ---- A. Planning gate ----------------------------------------------------- #
def test_a_planning_gate(tmp_repo):
    ctl = make_controller(tmp_repo)
    m = create_and_plan(ctl, "migrate X to Y", [F("F001", "Build foundation")], [MS("MS01", "m")])
    assert m.status == MissionStatus.AWAITING_APPROVAL
    assert not os.path.exists(os.path.join(tmp_repo, "F001.done")), "no implementation before approval"
    ctl.approve(m.id)
    ctl.run(m.id)
    m2 = store.load_mission(tmp_repo, m.id)
    assert m2.status == MissionStatus.COMPLETED
    assert os.path.exists(os.path.join(tmp_repo, "F001.done"))


# ---- B. Dependencies ------------------------------------------------------ #
def test_b_dependencies(tmp_repo):
    order = []

    def sim(feature, workdir):
        order.append(feature.id)
        return _ok(feature, workdir)

    ctl = make_controller(tmp_repo, simulator=sim)
    m = create_and_plan(ctl, "g",
                        [F("F001", "a"), F("F002", "b"), F("F003", "c", deps=["F001"])],
                        [MS("MS01", "m")])
    ctl.approve(m.id)
    ctl.run(m.id)
    assert "F001" in order and order.index("F001") < order.index("F003"), "F003 ran before F001"


# ---- C. Parallel workers + concurrency ceiling --------------------------- #
def test_c_parallel_workers_and_ceiling(tmp_repo):
    lock = threading.Lock()
    active = {"n": 0, "peak": 0}

    def sim(feature, workdir):
        with lock:
            active["n"] += 1
            active["peak"] = max(active["peak"], active["n"])
        time.sleep(0.03)
        with lock:
            active["n"] -= 1
        return _ok(feature, workdir)

    ctl = make_controller(tmp_repo, simulator=sim,
                          config_over={"execution": {"max_concurrent_workers": 2}})
    m = create_and_plan(ctl, "g",
                        [F("F001", "a", paths=["x"]), F("F002", "b", paths=["y"]),
                         F("F003", "c", paths=["z"]), F("F004", "d", paths=["w"])],
                        [MS("MS01", "m")])
    ctl.approve(m.id)
    ctl.run(m.id)
    m2 = store.load_mission(tmp_repo, m.id)
    assert all(m2.features[f].status == FeatureStatus.COMPLETED for f in ("F001", "F002", "F003", "F004"))
    assert active["peak"] <= 2, "concurrency ceiling exceeded"
    assert active["peak"] >= 2, "workers never ran concurrently"


# ---- D. Worker failure + retry ------------------------------------------- #
def test_d_worker_failure_and_retry(tmp_repo):
    attempts = {}

    def sim(feature, workdir):
        attempts[feature.id] = attempts.get(feature.id, 0) + 1
        if feature.id == "F001" and attempts[feature.id] == 1:
            return {"status": "failed", "summary": "boom", "notes": ["first try failed"], "changed_files": []}
        return _ok(feature, workdir)

    ctl = make_controller(tmp_repo, simulator=sim)
    m = create_and_plan(ctl, "g", [F("F001", "a")], [MS("MS01", "m")])
    ctl.approve(m.id)
    ctl.run(m.id)
    m2 = store.load_mission(tmp_repo, m.id)
    assert attempts["F001"] >= 2
    assert m2.features["F001"].status == FeatureStatus.COMPLETED
    assert m2.features["F001"].attempts >= 1
    assert m2.status == MissionStatus.COMPLETED


# ---- E. Validator catches defect -> corrective work ---------------------- #
def test_e_validator_catches_defect(tmp_repo):
    def sim(feature, workdir):
        if feature.is_fix:
            with open(os.path.join(workdir, "F001.done"), "w") as fh:
                fh.write("complete implementation\n")
            return _ok(feature, workdir)
        with open(os.path.join(workdir, "F001.done"), "w") as fh:
            fh.write("TODO(hamgoose) incomplete\n")
        return {"status": "completed", "summary": "claims done", "changed_files": ["F001.done"], "notes": []}

    ctl = make_controller(tmp_repo, simulator=sim)
    m = create_and_plan(ctl, "g", [F("F001", "a")], [MS("MS01", "m")])
    ctl.approve(m.id)
    ctl.run(m.id)
    m2 = store.load_mission(tmp_repo, m.id)
    fixes = [fid for fid in m2.features if fid.startswith("F001-FIX")]
    assert fixes, "expected a corrective feature to be created"
    assert m2.status == MissionStatus.COMPLETED
    assert any(e["type"] == "FIX_FEATURE_CREATED" for e in m2.events)


# ---- F. Pause / resume ---------------------------------------------------- #
def test_f_pause_resume(tmp_repo):
    ctl = make_controller(tmp_repo)
    m = create_and_plan(ctl, "g", [F("F001", "a"), F("F002", "b")], [MS("MS01", "m")])
    ctl.approve(m.id)
    ctl.pause(m.id, "user asked")
    out = ctl.run(m.id)
    assert "paused" in out.lower()
    m2 = store.load_mission(tmp_repo, m.id)
    assert m2.status == MissionStatus.PAUSED
    assert not os.path.exists(os.path.join(tmp_repo, "F001.done"))
    ctl.resume(m.id)
    ctl.run(m.id)
    m3 = store.load_mission(tmp_repo, m.id)
    assert m3.status == MissionStatus.COMPLETED


# ---- G. Process interruption / crash recovery ---------------------------- #
def test_g_crash_recovery(tmp_repo):
    ctl = make_controller(tmp_repo)
    m = create_and_plan(ctl, "g", [F("F001", "a")], [MS("MS01", "m")])
    ctl.approve(m.id)
    # simulate a crash mid-worker: F001 left RUNNING on disk
    m2 = store.load_mission(tmp_repo, m.id)
    m2.features["F001"].status = FeatureStatus.RUNNING
    store.save_mission(m2)
    # a fresh controller (new "process") resumes and reconciles
    ctl2 = make_controller(tmp_repo)
    ctl2.run(m.id)
    m3 = store.load_mission(tmp_repo, m.id)
    assert m3.features["F001"].status == FeatureStatus.COMPLETED
    assert "WORKER_RECONCILED" in [e["type"] for e in m3.events]


# ---- H. Steering (scheduler honors updated priorities) ------------------- #
def test_h_steering(tmp_repo):
    order = []

    def sim(feature, workdir):
        order.append(feature.id)
        return _ok(feature, workdir)

    ctl = make_controller(tmp_repo, simulator=sim,
                          config_over={"execution": {"max_concurrent_workers": 1}})
    m = create_and_plan(ctl, "g", [F("F001", "a"), F("F002", "b")], [MS("MS01", "m")])
    ctl.approve(m.id)
    ctl.steer(m.id, "prioritize F002", feature_id="F002", priority=1)
    ctl.run(m.id)
    assert order == ["F002", "F001"], "steering priority not honored"


# ---- I. Replanning -------------------------------------------------------- #
def test_i_replanning(tmp_repo):
    def sim(feature, workdir):
        return _ok(feature, workdir)

    ctl = make_controller(tmp_repo, simulator=sim)
    m = create_and_plan(ctl, "g",
                        [F("F001", "a", ms="MS01"), F("F002", "b", ms="MS02", deps=["F001"])],
                        [MS("MS01", "m1"), MS("MS02", "m2")])
    ctl.approve(m.id)
    ctl.run(m.id, max_steps=1)  # partial execution
    ctl.replan(m.id, "no longer using postgres",
               plan_delta={"keep": ["F001"], "supersede": ["F002"], "remove": [],
                           "new_features": [{"title": "SQLite storage", "milestone": "MS01",
                                              "description": "d", "acceptance_criteria": ["c"]}],
                           "new_milestones": [], "note": "switched to sqlite"})
    m2 = store.load_mission(tmp_repo, m.id)
    assert m2.features["F002"].status == FeatureStatus.SUPERSEDED
    assert m2.features["F001"].status == FeatureStatus.COMPLETED  # completed work preserved
    assert any("SQLite storage" in f.title for f in m2.features.values())
    assert m2.current_revision >= 2
    assert any(e["type"] == "MISSION_REPLANNED" for e in m2.events)


# ---- J. Git conflict ------------------------------------------------------ #
def test_j_git_conflict(git_repo):
    def sim(feature, workdir):
        with open(os.path.join(workdir, "shared.txt"), "w") as fh:
            fh.write(feature.id + "\n")  # both overwrite the same line
        return {"status": "completed", "summary": "ok", "changed_files": ["shared.txt"], "notes": []}

    ctl = make_controller(git_repo, simulator=sim, git=True,
                          config_over={"execution": {"max_concurrent_workers": 2}})
    m = create_and_plan(ctl, "g", [F("F001", "a", paths=["one"]), F("F002", "b", paths=["two"])],
                        [MS("MS01", "m")])
    ctl.approve(m.id)
    ctl.run(m.id, max_steps=10)
    m2 = store.load_mission(git_repo, m.id)
    conflicts = [f for f in m2.features.values() if (f.failure or "") == "MERGE_CONFLICT"]
    assert conflicts, "expected a merge conflict to be detected"
    for f in conflicts:
        assert f.status != FeatureStatus.COMPLETED, "conflict must not be recorded as success"
        assert m2.features["F001"].status == FeatureStatus.COMPLETED


# ---- K. Final validation gate -------------------------------------------- #
def test_k_final_validation_gate(tmp_repo):
    calls = {"n": 0}

    def checker(kind, mission, milestone_id, workdir):
        if kind == "final":
            calls["n"] += 1
            if calls["n"] == 1:
                return {"passed": False, "severity": "major",
                        "findings": [{"feature": "-", "criterion": "goal", "problem": "not met",
                                       "evidence": "", "recommended_fix": "fix"}],
                        "summary": "fail"}
            return {"passed": True, "severity": "none", "findings": [], "summary": "pass"}
        return {"passed": True, "severity": "none", "findings": [], "summary": "ok"}

    ctl = make_controller(tmp_repo, checker=checker)
    m = create_and_plan(ctl, "g", [F("F001", "a")], [MS("MS01", "m")])
    ctl.approve(m.id)
    ctl.run(m.id)
    m2 = store.load_mission(tmp_repo, m.id)
    assert m2.status == MissionStatus.COMPLETED
    assert calls["n"] >= 2, "mission completed before final validation passed"


# ---- L. Nested delegation constraint ------------------------------------- #
def test_l_no_nested_delegation(tmp_repo):
    from hamgoose import prompting
    from hamgoose.models import Feature, Milestone, Mission

    m = Mission(id="M1", goal="g", repo=".",
                milestones={"MS01": Milestone(id="MS01", objective="o", features=["F001"])},
                features={"F001": Feature(id="F001", title="a", milestone="MS01")})
    p = prompting.worker_prompt(m, m.features["F001"], {"enabled": False}, "")
    assert "MUST NOT delegate" in p
    import inspect

    from hamgoose import worker

    src = inspect.getsource(worker.GooseRunBackend)
    assert "goose\", \"run\"" in src, "worker must be a single isolated goose run"
    assert "--with-extension" not in src and "delegate" not in src
