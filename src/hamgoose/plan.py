"""Plan self-validation and readiness preflight.

Plan validation runs BEFORE a plan is presented for approval and catches
structural defects (cycles, self-deps, vague/oversized/micro features, dangling
deps). Readiness inspects the repository before a significant mission.
"""
from __future__ import annotations

import os
from typing import Dict, List

from . import git as gitmod
from .models import Feature, Mission

_VAGUE = {"update", "improve", "fix", "misc", "other", "do it", "general", "stuff", "handle"}
_MICRO = ("create file", "add import", "write helper", "rename file", "bump version", "add constant", "add a comment")


def has_cycle(features: Dict[str, Feature]) -> bool:
    from .scheduler import find_cycle

    return bool(find_cycle(features))


def validate_plan(mission: Mission) -> List[str]:
    issues: List[str] = []
    feats = mission.features

    # dangling / self dependencies
    for f in feats.values():
        for d in f.dependencies:
            if d == f.id:
                issues.append("{} has a self-dependency".format(f.id))
            elif d not in feats:
                issues.append("{} depends on unknown feature {}".format(f.id, d))

    # cycles
    if has_cycle(feats):
        issues.append("dependency cycle detected")

    for f in feats.values():
        title = f.title.strip().lower()
        if not f.title or len(f.title) < 6:
            issues.append("{} has a vague/empty title".format(f.id))
        elif title in _VAGUE and not f.acceptance_criteria:
            issues.append("{} title '{}' is vague with no acceptance criteria".format(f.id, f.title))
        if any(t in title for t in _MICRO):
            issues.append("{} looks like a meaningless micro-feature: {}".format(f.id, f.title))
        if not f.acceptance_criteria:
            issues.append("{} has no acceptance criteria".format(f.id))
        if len(f.description) > 1200 and not f.acceptance_criteria:
            issues.append("{} is oversized with no bounded criteria".format(f.id))
        if f.milestone and f.milestone not in mission.milestones:
            issues.append("{} references unknown milestone {}".format(f.id, f.milestone))

    # milestone boundaries
    for ms in mission.ordered_milestones():
        if not ms.features:
            issues.append("milestone {} is empty".format(ms.id))

    return issues


def fix_plan(mission: Mission) -> List[str]:
    """Best-effort structural fixes (drop self-deps and dangling deps, break one
    cycle edge). Returns a description of what was changed."""
    changed = []
    for f in list(mission.features.values()):
        before = list(f.dependencies)
        f.dependencies = [d for d in f.dependencies if d != f.id and d in mission.features]
        if f.dependencies != before:
            changed.append("removed invalid dependency from {}".format(f.id))
    # break at most one cycle edge
    from .scheduler import find_cycle

    guard = 0
    while find_cycle(mission.features) and guard < 50:
        cyc = find_cycle(mission.features)
        if not cyc:
            break
        node = cyc[-2]
        f = mission.features.get(node)
        if f and cyc[-1] in f.dependencies:
            f.dependencies.remove(cyc[-1])
            changed.append("broke cycle edge {} -> {}".format(node, cyc[-1]))
        else:
            break
        guard += 1
    return changed


# --------------------------------------------------------------------------- #
# Readiness
# --------------------------------------------------------------------------- #
def _has(root: str, *names) -> bool:
    for n in names:
        if os.path.exists(os.path.join(root, n)):
            return True
    return False


def check_readiness(repo: str) -> Dict[str, str]:
    report: Dict[str, str] = {}
    notes: List[str] = []
    gm = gitmod.GitManager(repo)
    is_repo = gm.is_repo()
    report["Git"] = "PASS" if is_repo else "FAIL"
    if is_repo and gm.is_dirty():
        report["Dirty working tree"] = "WARN"
        notes.append("working tree is dirty; base commit captured but uncommitted changes exist")
    else:
        report["Dirty working tree"] = "PASS"

    report["Build command"] = _detect("Build command", repo, ["Makefile", "package.json", "pyproject.toml", "Cargo.toml", "pom.xml", "go.mod", "setup.py"])
    report["Unit tests"] = _detect("Unit tests", repo, ["tests", "test", "spec", "pytest.ini", "tox.ini"])
    report["Lint/typecheck"] = _detect("Lint/typecheck", repo, [".eslintrc", "eslint.config.js", ".flake8", "ruff.toml", "pyrightconfig.json", ".prettierrc"])
    report["App startup"] = _detect("App startup", repo, ["README.md", "Makefile", "scripts"])
    report["User-flow automation"] = _detect("User-flow automation", repo, ["playwright", "cypress", "e2e", "puppeteer"])
    report["Project instructions"] = "PASS" if _has(repo, "AGENTS.md", ".goosehints", "CLAUDE.md", "CONTRIBUTING.md", "README.md") else "WARN"

    report["checks"] = list(report.keys())
    report["notes"] = notes
    return report


def _detect(name, repo, names) -> str:
    if _has(repo, *names):
        return "PASS"
    # look one level deep
    try:
        for entry in os.listdir(repo):
            if entry in names and os.path.isdir(os.path.join(repo, entry)):
                return "PASS"
    except OSError:
        pass
    return "WARN"
