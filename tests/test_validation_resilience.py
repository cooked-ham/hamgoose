"""H4: validator timeouts and inconclusive verdicts are infrastructure
outcomes - they must never consume the correction budget or create no-op fix
cycles, and they retry with a longer budget before blocking."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from harness import F, MS, create_and_plan, make_controller  # noqa: E402

from hamgoose import store  # noqa: E402
from hamgoose.models import Finding, MilestoneStatus, MissionStatus, ValidationResult  # noqa: E402
from hamgoose.validator import ValidationBackend  # noqa: E402


class _ScriptedValidation(ValidationBackend):
    """Per-kind result queues: each run(kind) pops the next result for that
    kind, mirroring the scrutiny+user_testing parallel rounds."""

    name = "scripted"

    def __init__(self, results):
        #: {"scrutiny": [res, ...], "user_testing": [...], "final": [...]}
        self.results = {k: list(v) for k, v in results.items()}
        self.timeouts = []

    def run(self, kind, mission, milestone_id, base, head, workdir, project_context, timeout=None):
        self.timeouts.append(timeout)
        res = self.results[kind].pop(0)
        return ValidationResult(kind=kind, passed=res.get("passed", False),
                                severity=res.get("severity", "none"),
                                findings=[Finding(**f) for f in res.get("findings", [])],
                                summary=res.get("summary", ""),
                                timed_out=res.get("timed_out", False))


def T():
    return {"passed": False, "timed_out": True}


def _running_mission(tmp_path, results_by_kind):
    ctl = make_controller(tmp_path)
    m = create_and_plan(ctl, "g", [F("F001", "t")], [MS("MS01", "o")])
    ctl.approve(m.id)
    ctl.validation_backend = _ScriptedValidation(results_by_kind)
    return ctl, ctl._get(m.id)


def test_validator_timeout_retries_without_correction_budget(tmp_path):
    ctl, m = _running_mission(tmp_path, {
        "scrutiny": [T(), T()],
        "user_testing": [T(), T()],
    })
    ctl.run(m.id, max_steps=2)  # dispatch + first validation round
    m = ctl._get(m.id)
    ms = m.milestones[m.active_milestone]
    assert m.correction_attempts == 0 and ms.correction_attempts == 0
    assert ms.validation_infra_retries == 1
    assert ms.status == MilestoneStatus.RUNNING  # retry scheduled
    types = [e["type"] for e in store.read_events(str(tmp_path), m.id)]
    assert "VALIDATION_TIMEOUT" in types
    assert not any(t == "MISSION_BLOCKED" for t in types)

    # second timeout: blocked with the validator reason, still no corrections
    ctl.run(m.id, max_steps=2)
    m = ctl._get(m.id)
    ms = m.milestones["MS01"]
    assert ms.status == MilestoneStatus.BLOCKED
    assert "validation infrastructure failed" in m.block_reason
    assert "validator timeout" in m.block_reason
    assert m.correction_attempts == 0


def test_validator_timeout_retry_gets_longer_budget(tmp_path):
    ctl, m = _running_mission(tmp_path, {
        "scrutiny": [T(), {"passed": True}],
        "user_testing": [T(), {"passed": True}],
        "final": [{"passed": True}],
    })
    backend = ctl.validation_backend
    ctl.run(m.id, max_steps=2)  # round 1: both validators time out
    ctl.run(m.id, max_steps=2)  # round 2 runs at the doubled budget
    # timeouts[0..1] = scrutiny+user_testing round 1; [2..3] = round 2
    assert backend.timeouts[3] == backend.timeouts[1] * 2  # genuine second chance
    m = ctl._get(m.id)
    assert m.milestones["MS01"].status == MilestoneStatus.PASSED
    assert m.status == MissionStatus.COMPLETED  # final verdict passed cleanly


def test_zero_finding_failure_is_inconclusive_not_corrective(tmp_path):
    """The MS01 cascade: verdict failed, findings=[] -> three wasted rounds and
    a false BLOCK. Now: one retry, then a truthful block, no burned budget."""
    failed_empty = {"passed": False, "findings": [],
                    "summary": "validator produced no structured verdict"}
    ctl, m = _running_mission(tmp_path, {
        "scrutiny": [dict(failed_empty), dict(failed_empty)],
        "user_testing": [dict(failed_empty), dict(failed_empty)],
    })
    ctl.run(m.id, max_steps=2)
    m = ctl._get(m.id)
    ms = m.milestones[m.active_milestone]
    assert m.correction_attempts == 0
    assert ms.validation_infra_retries == 1
    types = [e["type"] for e in store.read_events(str(tmp_path), m.id)]
    assert "VALIDATION_INCONCLUSIVE" in types
    assert not [e for e in store.read_events(str(tmp_path), m.id)
                if e["type"] == "FIX_FEATURE_CREATED"]

    ctl.run(m.id, max_steps=2)
    m = ctl._get(m.id)
    ms = m.milestones["MS01"]
    assert ms.status == MilestoneStatus.BLOCKED
    assert "no actionable findings" in m.block_reason


def test_conclusive_failure_with_findings_still_creates_fixes(tmp_path):
    """Regression guard: real findings keep the normal corrective flow."""
    ctl, m = _running_mission(tmp_path, {
        "scrutiny": [
            {"passed": False, "severity": "major",
             "findings": [{"feature": "F001", "criterion": "c", "problem": "p",
                           "evidence": "e", "recommended_fix": "rf"}]},
            {"passed": True},
        ],
        "user_testing": [{"passed": True}, {"passed": True}],
        "final": [{"passed": True}],
    })
    ctl.run(m.id, max_steps=2)
    m = ctl._get(m.id)
    assert m.correction_attempts == 1
    assert any(f.is_fix for f in m.features.values())
    assert "VALIDATION_FAILED" in [e["type"] for e in store.read_events(str(tmp_path), m.id)]


def test_mixed_timeout_and_pass_does_not_fail_milestone(tmp_path):
    """scrutiny passes, user_testing times out: no findings, no correction -
    the round is inconclusive and retried."""
    ctl, m = _running_mission(tmp_path, {
        "scrutiny": [{"passed": True}, {"passed": True}],
        "user_testing": [T(), {"passed": True}],
        "final": [{"passed": True}],
    })
    ctl.run(m.id, max_steps=2)
    m = ctl._get(m.id)
    ms = m.milestones[m.active_milestone]
    assert m.correction_attempts == 0
    assert ms.scrutiny_status in ("pending", "passed")  # never "failed"
    ctl.run(m.id, max_steps=2)
    m = ctl._get(m.id)
    assert m.milestones["MS01"].status == MilestoneStatus.PASSED


def test_final_validation_timeout_does_not_create_fix_milestone(tmp_path):
    ctl = make_controller(tmp_path)
    m = create_and_plan(ctl, "g", [F("F001", "t")], [MS("MS01", "o")])
    ctl.approve(m.id)
    ctl.validation_backend = _ScriptedValidation({
        "scrutiny": [{"passed": True}, {"passed": True}],
        "user_testing": [{"passed": True}, {"passed": True}],
        "final": [T(), T()],
    })
    ctl.run(m.id, max_steps=2)  # dispatch + milestone validation (passed)
    m = ctl._get(m.id)
    assert m.milestones["MS01"].status == MilestoneStatus.PASSED

    ctl.run(m.id, max_steps=1)  # final round 1 (timeout) -> infra retry
    m = ctl._get(m.id)
    assert m.status == MissionStatus.RUNNING  # not failed by a dead validator
    assert m.validation_retries == 1

    ctl.run(m.id, max_steps=1)  # final round 2 (timeout again) -> blocked
    m = ctl._get(m.id)
    assert m.status == MissionStatus.BLOCKED
    assert "validator timeout" in m.block_reason
    assert not any(ms.objective == "Final corrections" for ms in m.milestones.values())
    assert m.correction_attempts == 0
