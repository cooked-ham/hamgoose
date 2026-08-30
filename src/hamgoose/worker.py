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


class WorkerBackend(ABC):
    name = "base"

    @abstractmethod
    def run(self, prompt: str, workdir: str, role: Dict[str, Any], feature: Feature, timeout: Optional[int]) -> WorkerResult:
        ...


class GooseRunBackend(WorkerBackend):
    name = "goose_run"

    def run(self, prompt: str, workdir: str, role: Dict[str, Any], feature: Feature, timeout: Optional[int]) -> WorkerResult:
        rid = worker_id()
        fd, path = tempfile.mkstemp(suffix=".md")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(prompt)
            cmd = ["goose", "run", "-i", path, "--output-format", "json", "--no-session",
                   "--max-turns", str(role.get("max_turns", 100))]
            if role.get("provider"):
                cmd += ["--provider", role.get("provider")]
            if role.get("model"):
                cmd += ["--model", role.get("model")]
            feature.worker.run_id = rid
            try:
                stdout, stderr, exit_code, timed_out = gosub.run_captured(cmd, cwd=workdir, timeout=timeout)
            except OSError as e:
                return WorkerResult(status="unknown", raw=str(e), run_id=rid, exit_code=None, backend=self.name)

            text = extract_text(stdout, stderr)
            res = parse_worker_output(text, run_id=rid, backend=self.name)
            res.exit_code = exit_code
            res.timed_out = timed_out
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

    def run(self, prompt: str, workdir: str, role: Dict[str, Any], feature: Feature, timeout: Optional[int]) -> WorkerResult:
        rid = worker_id()
        try:
            data = self.simulator(feature, workdir) or {}
        except Exception as e:  # simulate a crash
            return WorkerResult(status="unknown", raw="crash: {}".format(e), run_id=rid, backend=self.name, exit_code=1)
        res = parse_worker_output(
            "```json\n{}\n```".format(__import__("json").dumps(data)), run_id=rid, backend=self.name
        )
        res.exit_code = 0
        return res
