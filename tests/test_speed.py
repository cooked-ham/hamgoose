"""Fast-profile and validation-parallelism regression tests."""
import threading
import time

from harness import F, MS, create_and_plan, make_controller
from hamgoose.config import Config
from hamgoose.models import ValidationResult
from hamgoose.validator import ValidationBackend


def test_defaults_are_bounded_for_interactive_runs():
    cfg = Config()
    assert cfg.orchestrator.max_turns == 32
    assert cfg.worker.max_turns == 32
    assert cfg.validator.max_turns == 32
    # H3/H4: 420/180 s killed Qwen3.8-class workers/validators mid-flight;
    # defaults now cover small-output-budget models (caps are inert for fast
    # models since a run ends when the model finishes).
    assert cfg.execution.semantic_timeout == 600
    assert cfg.execution.worker_timeout == 900
    assert cfg.execution.planner_timeout == 600
    assert cfg.execution.max_steps_per_run == 6
    assert cfg.execution.max_feature_attempts == 3
    assert cfg.validation.max_correction_attempts == 3


def test_feature_attempt_cap_is_applied_to_generated_plan(tmp_path):
    ctl = make_controller(str(tmp_path), config_over={"execution": {"max_feature_attempts": 1}})
    mission = create_and_plan(ctl, "g", [F("F001", "Build foundation")], [MS("MS01", "m")])
    assert mission.features["F001"].max_attempts == 1


class _ParallelValidationBackend(ValidationBackend):
    name = "parallel-test"

    def __init__(self):
        self._lock = threading.Lock()
        self.active = 0
        self.peak = 0

    def run(self, kind, mission, milestone_id, base, head, workdir, project_context):
        with self._lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
        try:
            time.sleep(0.05)
            return ValidationResult(kind=kind, passed=True, summary="ok")
        finally:
            with self._lock:
                self.active -= 1


def test_milestone_validators_run_concurrently(tmp_path):
    ctl = make_controller(str(tmp_path))
    backend = _ParallelValidationBackend()
    ctl.validation_backend = backend
    mission = create_and_plan(ctl, "g", [F("F001", "Build foundation")], [MS("MS01", "m")])
    ctl.approve(mission.id)
    ctl.run(mission.id, max_steps=1)  # worker only
    ctl.run(mission.id)              # milestone validators + final validator
    assert backend.peak == 2
