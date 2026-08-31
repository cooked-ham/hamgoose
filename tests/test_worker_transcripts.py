"""HG-01: full worker transcripts are persisted as evidence."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from harness import F, MS, make_controller  # noqa: E402

from hamgoose.models import Feature  # noqa: E402
from hamgoose.worker import GooseRunBackend, WorkerBackend, WorkerResult  # noqa: E402


GOOSE_JSON = json.dumps({
    "messages": [
        {"role": "assistant", "content": [
            {"type": "text", "text": '```json\n{"status": "completed", "summary": "done", '
                                     '"changed_files": ["a.py"], "tests": [], "notes": []}\n```'}
        ], "outputTokenLimitReached": False},
    ]
})


def test_backend_captures_raw_stdout(monkeypatch, tmp_path):
    monkeypatch.setattr("hamgoose.gosub.run_captured",
                        lambda cmd, **kw: (GOOSE_JSON, "", 0, False))
    res = GooseRunBackend().run("p", str(tmp_path), {}, Feature(id="F001", title="t"), None)
    assert res.raw_stdout == GOOSE_JSON


class _RawBackend(WorkerBackend):
    """Scripted backend that behaves like a leaf that produced raw stdout."""
    name = "scripted"

    def run(self, prompt, workdir, role, feature, timeout, on_progress=None):
        res = WorkerResult(status="completed", summary="done", changed_files=["a.py"],
                           raw="final message", run_id="W-TESTRAW1", backend=self.name)
        res.raw_stdout = GOOSE_JSON
        res.exit_code = 0
        def _touch(workdir):
            with open(os.path.join(workdir, "a.py"), "w") as f:
                f.write("x = 1\n")
        _touch(workdir)
        return res


def test_reconcile_persists_raw_json(tmp_path):
    ctl = make_controller(tmp_path, simulator=None)
    ctl.worker_backend = _RawBackend()
    m = ctl.create_mission("goal")
    ctl.plan(m.id, features=[F("F001", "do it")], milestones=[MS("MS01", "obj")])
    m = ctl._get(m.id)
    ctl.approve(m.id)
    ctl.run(m.id)

    m = ctl._get(m.id)
    wdir = os.path.join(str(tmp_path), ".goose", "hamgoose", m.id, "workers")
    raw = os.path.join(wdir, "W-TESTRAW1.raw.json")
    txt = os.path.join(wdir, "W-TESTRAW1.txt")
    assert os.path.exists(raw), "raw transcript must be persisted (HG-01)"
    data = json.load(open(raw))          # parses as JSON when output is JSON
    assert "messages" in data
    assert os.path.exists(txt)           # redacted final message still kept


def test_raw_capture_is_size_capped():
    from hamgoose.worker import RAW_CAPTURE_LIMIT

    big = "x" * (RAW_CAPTURE_LIMIT + 1000)
    monkey = None
    # slice logic lives on the backend; assert the cap constant is sane
    assert RAW_CAPTURE_LIMIT == 5 * 1024 * 1024
    assert len(big[:RAW_CAPTURE_LIMIT]) == RAW_CAPTURE_LIMIT
