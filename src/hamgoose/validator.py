"""Validators: independent, distrustful verification of real repository state.

Two conceptually separate roles, each in a fresh isolated Goose context:

- Scrutiny: distrusts worker claims and inspects code/diff/build/tests.
- User-testing: exercises the app from the user's perspective (CLI/API/browser).

Backends:
- GooseRunValidationBackend (production): runs the validator prompt in an isolated
  `goose run` with tools, parses the structured JSON verdict.
- MockValidationBackend (tests): deterministic; passes when the expected artifacts
  exist and no placeholder/TODO markers remain, so the validation + corrective
  loop is fully testable without a live model.
"""
from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import asdict
from typing import Any, Callable, Dict, Optional

from . import prompting, redact
from .models import Finding, Mission, ValidationResult


class ValidationBackend(ABC):
    name = "base"

    @abstractmethod
    def run(self, kind: str, mission: Mission, milestone_id: str, base: str, head: str, workdir: str, project_context: str) -> ValidationResult:
        ...


class GooseRunValidationBackend(ValidationBackend):
    name = "goose_run"

    def __init__(self, semantic):
        # `semantic` is either a SemanticClient-like instance or a callable
        # (mission) -> client. The controller passes the callable form so the
        # validator's provider/model/timeouts resolve from the MISSION's
        # effective config (H5), not from controller-level defaults that never
        # saw mission overrides.
        self._semantic = semantic

    def _client(self, mission: Mission):
        return self._semantic(mission) if callable(self._semantic) else self._semantic

    def _timeout_for(self, mission: Mission, milestone_id: str, timeout: Optional[int]) -> int:
        """Effective validator budget: mission config base (H5), doubled per
        infrastructure retry (H4) so the retry is a genuine second chance."""
        if timeout is not None:
            return int(timeout)
        base = 600
        try:
            from .config import Config

            base = Config.load(mission.config or None, repo=mission.repo).execution.semantic_timeout
        except Exception:
            pass
        ms = (mission.milestones or {}).get(milestone_id)
        retries = getattr(ms, "validation_infra_retries", 0) or 0
        return base * (retries + 1)

    def run(self, kind, mission, milestone_id, base, head, workdir, project_context, timeout: Optional[int] = None):
        if kind == "scrutiny":
            prompt = prompting.scrutiny_prompt(mission, milestone_id, base, head, project_context)
        elif kind == "user_testing":
            prompt = prompting.user_test_prompt(mission, milestone_id, base, head, project_context)
        else:
            prompt = prompting.final_validation_prompt(mission, base, head, project_context)
        client = self._client(mission)
        if hasattr(client, "complete_detailed"):
            res = client.complete_detailed(prompt, role="validator",
                                           timeout=self._timeout_for(mission, milestone_id, timeout))
            return _parse_validation(kind, res.text, timed_out=res.timed_out)
        text = client.complete(prompt, role="validator")  # legacy/test client
        return _parse_validation(kind, text)


def _parse_validation(kind: str, text: str, timed_out: bool = False) -> ValidationResult:
    from .semantic import extract_json

    data = extract_json(text or "")
    raw = redact.redact(text or "")
    if not data:
        if timed_out:
            # H4: a kill at the time budget is an infrastructure outcome. It is
            # recorded, but it is NOT a major quality verdict - the controller
            # branches on timed_out before the corrective loop.
            return ValidationResult(
                kind=kind, passed=False, severity="none",
                summary="validator timeout - no structured verdict (infrastructure, not a quality failure)",
                raw=raw, timed_out=True,
            )
        return ValidationResult(kind=kind, passed=False, severity="major", summary="validator produced no structured verdict", raw=raw)
    findings = [
        Finding(
            feature=str(f.get("feature", "-")),
            criterion=str(f.get("criterion", "")),
            problem=str(f.get("problem", "")),
            evidence=str(f.get("evidence", "")),
            recommended_fix=str(f.get("recommended_fix", "")),
        )
        for f in (data.get("findings") or [])
    ]
    passed = data.get("passed", False)
    if isinstance(passed, str):
        passed = passed.strip().lower() in {"true", "1", "yes", "passed"}
    else:
        passed = bool(passed)
    return ValidationResult(
        kind=kind,
        passed=passed,
        severity=str(data.get("severity", "none")),
        findings=findings,
        summary=str(data.get("summary", "")),
        raw=raw,
    )


_SCAN_EXTS = (".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".rs", ".rb", ".java", ".c", ".cpp", ".done", ".txt", ".md", ".json")


def _default_checker(kind: str, mission: Mission, milestone_id: str, workdir: str) -> Dict[str, Any]:
    findings = []
    ok = True
    if os.path.isdir(workdir):
        for root, _dirs, files in os.walk(workdir):
            if ".goose" in root or os.sep + ".git" in root:
                continue
            for name in files:
                if not name.endswith(_SCAN_EXTS):
                    continue
                path = os.path.join(root, name)
                try:
                    content = open(path, encoding="utf-8", errors="ignore").read()
                except OSError:
                    continue
                if "TODO(hamgoose)" in content or "INCOMPLETE" in content:
                    ok = False
                    findings.append(
                        Finding(feature="milestone", criterion="no placeholder code",
                                problem="placeholder/TODO found in implementation",
                                evidence=os.path.relpath(path, workdir),
                                recommended_fix="replace placeholder with a real implementation")
                    )
    return {"passed": ok, "severity": "none" if ok else "major", "summary": "mock validation",
            "findings": [asdict(f) for f in findings]}


class MockValidationBackend(ValidationBackend):
    name = "mock"

    def __init__(self, checker: Optional[Callable[..., Dict[str, Any]]] = None):
        self.checker = checker or _default_checker

    def run(self, kind, mission, milestone_id, base, head, workdir, project_context, timeout: Optional[int] = None):
        data = self.checker(kind, mission, milestone_id, workdir) or {}
        passed = data.get("passed", False)
        if isinstance(passed, str):
            passed = passed.strip().lower() in {"true", "1", "yes", "passed"}
        else:
            passed = bool(passed)
        findings = [Finding(**f) for f in (data.get("findings") or [])]
        return ValidationResult(
            kind=kind,
            passed=passed,
            severity=str(data.get("severity", "none")),
            findings=findings,
            summary=str(data.get("summary", "mock")),
            raw="",
        )
