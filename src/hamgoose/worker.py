"""Worker execution: launches isolated Goose workers and reconciles their results.

A worker is a leaf Goose context (its own process/session) that implements one
tightly-scoped feature. Two backends:

- GooseRunBackend (production): spawns `goose run` as a subprocess in the
  feature's worktree/repo, captures output, enforces timeouts, and is
  individually identifiable and cancellable.
- MockBackend (tests): a deterministic stand-in so the full orchestration path
  (scheduling, git, validation loop, pause/resume, replanning, crash recovery)
  can be exercised without a live model.

A worker claiming "done" is NOT evidence of completion; the controller inspects
the actual repository/commits before accepting a feature.
"""
from __future__ import annotations

import os
import re
import tempfile
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from . import gosub, redact
from .ids import worker_id
from .models import Feature
from .semantic import extract_json, extract_text


@dataclass
class WorkerResult:
    status: str = "unknown"  # completed | failed | blocked | unknown
    summary: str = ""
    changed_files: List[str] = field(default_factory=list)
    tests: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    blocked_reason: str = ""
    raw: str = ""
    #: full raw `goose run` stdout, size-capped (HG-01): persisted as .raw.json
    #: BEFORE parsing so classification bugs cannot destroy the evidence.
    raw_stdout: str = ""
    exit_code: Optional[int] = None
    timed_out: bool = False
    run_id: str = ""
    pid: Optional[int] = None
    backend: str = ""

    @property
    def claimed_ok(self) -> bool:
        return self.status == "completed"


def parse_worker_output(text: str, run_id: str = "", backend: str = "") -> WorkerResult:
    data = extract_json(text or "")
    raw = redact.redact(text or "")
    if not data:
        return WorkerResult(status="unknown", raw=raw, run_id=run_id, backend=backend)
    return WorkerResult(
        status=str(data.get("status", "unknown")),
        summary=str(data.get("summary", "")),
        changed_files=[str(x) for x in (data.get("changed_files") or [])],
        tests=[str(x) for x in (data.get("tests") or [])],
        notes=[str(x) for x in (data.get("notes") or [])],
        blocked_reason=str(data.get("blocked_reason", "")),
        raw=raw,
        run_id=run_id,
        backend=backend,
    )


#: cap for raw transcript capture (5 MB) — evidence without unbounded disk use
RAW_CAPTURE_LIMIT = 5 * 1024 * 1024


class WorkerBackend(ABC):
    name = "base"

    @abstractmethod
    def run(
        self,
        prompt: str,
        workdir: str,
        role: Dict[str, Any],
        feature: Feature,
        timeout: Optional[int],
        on_progress: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> WorkerResult:
        ...


class GooseRunBackend(WorkerBackend):
    name = "goose_run"

    def run(
        self,
        prompt: str,
        workdir: str,
        role: Dict[str, Any],
        feature: Feature,
        timeout: Optional[int],
        on_progress: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> WorkerResult:
        rid = worker_id()
        fd, path = tempfile.mkstemp(suffix=".md")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(prompt)
            cmd = ["goose", "run", "-i", path, "--output-format", "json", "--quiet", "--no-session",
                   "--max-turns", str(role.get("max_turns", 100))]
            # "inherit" is a hamgoose sentinel, not a Goose provider name.
            if role.get("provider") and role.get("provider") != "inherit":
                cmd += ["--provider", role.get("provider")]
            if role.get("model") and role.get("model") != "inherit":
                cmd += ["--model", role.get("model")]
            feature.worker.run_id = rid

            def _poll(out_path: str, _err_path: str, elapsed: float) -> None:
                # HG-14: surface mid-run progress (bytes + a cheap turn hint)
                # instead of 420 s of silence. Best-effort only.
                if on_progress is None:
                    return
                try:
                    with open(out_path, "rb") as fh:
                        fh.seek(0, os.SEEK_END)
                        nbytes = fh.tell()
                    with open(out_path, encoding="utf-8", errors="replace") as fh:
                        chunk = fh.read()
                except OSError:
                    return
                turn_hint = chunk.count('"role"')
                on_progress({"run_id": rid, "bytes": nbytes, "turn_hint": turn_hint, "elapsed": round(elapsed, 1)})

            try:
                stdout, stderr, exit_code, timed_out = gosub.run_captured(
                    cmd, cwd=workdir, timeout=timeout, on_poll=_poll if on_progress else None
                )
            except OSError as e:
                return WorkerResult(status="unknown", raw=str(e), run_id=rid, exit_code=None, backend=self.name)

            raw_stdout = (stdout or "")[:RAW_CAPTURE_LIMIT]
            text = extract_text(stdout, stderr)
            res = parse_worker_output(text, run_id=rid, backend=self.name)
            res.exit_code = exit_code
            res.timed_out = timed_out
            res.raw_stdout = raw_stdout
            if exit_code not in (0, None) and res.status == "unknown":
                res.status = "failed"
            return res
        finally:
            try:
                os.remove(path)
            except OSError:
                pass


def _default_simulator(feature: Feature, workdir: str) -> Dict[str, Any]:
    """Deterministic default: create a marker file so git can observe a change."""
    target = os.path.join(workdir, feature.id + ".done")
    with open(target, "w", encoding="utf-8") as f:
        f.write("feature {} implemented\n".format(feature.id))
    return {"status": "completed", "summary": "implemented {}".format(feature.title),
            "changed_files": [feature.id + ".done"], "tests": [], "notes": []}


class MockBackend(WorkerBackend):
    name = "mock"

    def __init__(self, simulator: Optional[Callable[[Feature, str], Dict[str, Any]]] = None):
        self.simulator = simulator or _default_simulator

    def run(
        self,
        prompt: str,
        workdir: str,
        role: Dict[str, Any],
        feature: Feature,
        timeout: Optional[int],
        on_progress: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> WorkerResult:
        rid = worker_id()
        # emit one synthetic progress tick so WORKER_PROGRESS (HG-14) is
        # testable without a live leaf process
        if on_progress is not None:
            try:
                on_progress({"run_id": rid, "bytes": 128, "turn_hint": 1, "elapsed": 0.0})
            except Exception:
                pass
        try:
            data = self.simulator(feature, workdir) or {}
        except Exception as e:  # simulate a crash
            return WorkerResult(status="unknown", raw="crash: {}".format(e), run_id=rid, backend=self.name, exit_code=1)
        res = parse_worker_output(
            "```json\n{}\n```".format(__import__("json").dumps(data)), run_id=rid, backend=self.name
        )
        res.exit_code = 0
        return res
