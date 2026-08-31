"""HG-14: mid-run WORKER_PROGRESS events - no more 420 s of silence."""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from harness import F, MS, make_controller  # noqa: E402

from hamgoose import store  # noqa: E402


def _slow_simulator(feature, workdir):
    time.sleep(0.15)  # slow enough to be "running", fast for the suite
    with open(os.path.join(workdir, feature.id + ".done"), "w") as f:
        f.write("done\n")
    return {"status": "completed", "summary": "ok", "changed_files": [feature.id + ".done"],
            "tests": [], "notes": []}


def test_progress_events_emitted_and_stop(tmp_path):
    ctl = make_controller(tmp_path, simulator=_slow_simulator)
    m = ctl.create_mission("g")
    ctl.plan(m.id, features=[F("F001", "t")], milestones=[MS("MS01", "o")])
    ctl.approve(m.id)
    ctl.run(m.id)

    evs = [e for e in store.read_events(str(tmp_path), m.id) if e["type"] == "WORKER_PROGRESS"]
    assert evs, "a running worker must be observable (HG-14)"
    p = evs[0]["payload"]
    assert p["feature"] == "F001"
    assert p["run_id"]
    assert "bytes" in p and "elapsed" in p and "turn_hint" in p

    # terminal state: no progress events arrive after the mission is done
    n_after = len([e for e in store.read_events(str(tmp_path), m.id) if e["type"] == "WORKER_PROGRESS"])
    ctl.run(m.id)  # idempotent second run
    n_again = len([e for e in store.read_events(str(tmp_path), m.id) if e["type"] == "WORKER_PROGRESS"])
    assert n_again == n_after


def test_progress_deduped_when_unchanged(tmp_path):
    """The controller dedupes identical progress signatures."""
    seen = []

    class _TickingBackend(type(make_controller(tmp_path).worker_backend)):
        name = "ticking"

        def run(self, prompt, workdir, role, feature, timeout, on_progress=None):
            if on_progress:
                for _ in range(3):  # three IDENTICAL ticks
                    on_progress({"run_id": "W-T1", "bytes": 100, "turn_hint": 1, "elapsed": 1.0})
            with open(os.path.join(workdir, "x.done"), "w") as f:
                f.write("x")
            from hamgoose.worker import WorkerResult
            return WorkerResult(status="completed", summary="ok", changed_files=["x.done"],
                                raw="ok", run_id="W-T1", backend=self.name, exit_code=0)

    ctl = make_controller(tmp_path)
    ctl.worker_backend = _TickingBackend()
    m = ctl.create_mission("g")
    ctl.plan(m.id, features=[F("F001", "t")], milestones=[MS("MS01", "o")])
    ctl.approve(m.id)
    ctl.run(m.id)
    seen = [e for e in store.read_events(str(tmp_path), m.id) if e["type"] == "WORKER_PROGRESS"]
    assert len(seen) == 1  # deduped
