"""HG-04: model output-token-limit deaths are classified and retryable, and
the timeout boundary honors a 10 s wall-clock grace."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from harness import F, MS, make_controller  # noqa: E402

from hamgoose.models import FailureClass, Feature, RETRYABLE_FAILURES  # noqa: E402
from hamgoose.worker import WorkerResult  # noqa: E402


def _ctl(tmp_path):
    return make_controller(tmp_path)


def _feature():
    return Feature(id="F001", title="wire the thing")


def test_model_limit_failure_is_classified(tmp_path):
    ctl = _ctl(tmp_path)
    res = WorkerResult(status="completed", summary="...",
                       changed_files=["a.py"],
                       raw='analysis... {"outputTokenLimitReached": false}',
                       raw_stdout='{"outputTokenLimitReached": true}', exit_code=0)
    cls = ctl._classify(_feature(), res, changed=True, conflict=False)
    assert cls == FailureClass.MODEL_LIMIT_FAILURE
    assert FailureClass.MODEL_LIMIT_FAILURE in RETRYABLE_FAILURES


def test_model_limit_marker_in_redacted_raw_alone(tmp_path):
    ctl = _ctl(tmp_path)
    res = WorkerResult(status="completed", changed_files=["a.py"],
                       raw='{"outputTokenLimitReached": true}', exit_code=0)
    assert ctl._classify(_feature(), res, changed=True, conflict=False) == FailureClass.MODEL_LIMIT_FAILURE


def test_finish_reason_length_is_model_limit(tmp_path):
    ctl = _ctl(tmp_path)
    res = WorkerResult(status="completed", changed_files=["a.py"],
                       raw='chunk {"finish_reason": "length"} tail', exit_code=0)
    assert ctl._classify(_feature(), res, changed=True, conflict=False) == FailureClass.MODEL_LIMIT_FAILURE


def test_clean_limit_false_is_not_model_limit(tmp_path):
    ctl = _ctl(tmp_path)
    res = WorkerResult(status="completed", changed_files=["a.py"],
                       raw='{"outputTokenLimitReached": false}', raw_stdout='{"outputTokenLimitReached": false}',
                       exit_code=0)
    assert ctl._classify(_feature(), res, changed=True, conflict=False) is None


def test_boundary_fixture_420_8s_is_timeout(tmp_path):
    """The F002 attempt-3 fixture: 420.8 s wall time vs 420 s kill budget."""
    ctl = _ctl(tmp_path)
    res = WorkerResult(status="completed", changed_files=["a.py"], raw="ok", exit_code=0, timed_out=False)
    cls = ctl._classify(_feature(), res, changed=True, conflict=False,
                        duration=420.8, worker_timeout=420)
    assert cls == FailureClass.WORKER_TIMEOUT


def test_duration_below_grace_is_not_timeout(tmp_path):
    ctl = _ctl(tmp_path)
    res = WorkerResult(status="completed", changed_files=["a.py"], raw="ok", exit_code=0)
    assert ctl._classify(_feature(), res, changed=True, conflict=False,
                         duration=300.0, worker_timeout=420) is None


def test_clean_done_with_changes_is_accepted(tmp_path):
    ctl = _ctl(tmp_path)
    res = WorkerResult(status="completed", summary="did it", changed_files=["a.py"],
                       raw="did it", exit_code=0)
    assert ctl._classify(_feature(), res, changed=True, conflict=False,
                         duration=100.0, worker_timeout=420) is None


def test_model_limit_retry_keeps_truncated_message(tmp_path):
    """Full path: a limit-death keeps the truncated tail in failure_detail so
    the retry prompt carries the resume instruction (HG-04/HG-05)."""
    ctl = make_controller(tmp_path, simulator=lambda f, wd: {"status": "completed",
                                                             "summary": "cut off mid analysis",
                                                             "changed_files": [],
                                                             "notes": []})

    class _LimitBackend(type(ctl.worker_backend)):
        name = "limit"

        def run(self, prompt, workdir, role, feature, timeout, on_progress=None):
            with open(os.path.join(workdir, "touched.py"), "w") as fh:
                fh.write("x=1")
            res = WorkerResult(status="completed", summary="cut off",
                               changed_files=["touched.py"], raw="cut off mid analysis",
                               raw_stdout='{"outputTokenLimitReached": true}',
                               run_id="W-LIM1", backend=self.name, exit_code=0)
            return res

    ctl.worker_backend = _LimitBackend()
    m = ctl.create_mission("g")
    ctl.plan(m.id, features=[F("F001", "do it")], milestones=[MS("MS01", "o")])
    ctl.approve(m.id)
    ctl.run(m.id, max_steps=1)  # observe the FIRST failure + retry decision
    m = ctl._get(m.id)
    f = m.features["F001"]
    assert f.failure == "MODEL_LIMIT_FAILURE"
    assert f.attempts == 1
    assert f.status.value == "NEEDS_FIX"  # retryable

    from hamgoose import prompting
    prompt = prompting.worker_prompt(m, f, {"enabled": False}, "")
    assert "RESUMING A CUT-OFF RUN" in prompt
    assert "Do NOT re-analyze" in prompt
    assert "cut off" in prompt  # truncated tail forwarded
