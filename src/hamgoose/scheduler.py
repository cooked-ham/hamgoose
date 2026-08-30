"""Dependency-aware DAG scheduler with conflict detection and a concurrency cap.

A feature is READY only when every required dependency is COMPLETED. Before
parallel dispatch, features whose likely affected paths overlap are grouped so
conflicting work runs sequentially. The concurrency ceiling is always respected.
"""
from __future__ import annotations

from typing import Dict, List

from .models import Feature, FeatureStatus, Mission


def _norm(paths: List[str]) -> List[str]:
    return [p.strip().strip("/") for p in paths if p and p.strip()]


def _paths_overlap(a: List[str], b: List[str]) -> bool:
    na, nb = set(_norm(a)), set(_norm(b))
    if not na or not nb:
        return False  # unknown scope: assume no conflict (documented limitation)
    for x in na:
        for y in nb:
            if x == y or x.startswith(y + "/") or y.startswith(x + "/"):
                return True
    return False


def _dep_ok(mission: Mission, dep_id: str) -> bool:
    dep = mission.features.get(dep_id)
    return dep is not None and dep.status == FeatureStatus.COMPLETED


def ready_features(mission: Mission, milestone_id: str) -> List[Feature]:
    """Features in the milestone that can start: not terminal/superseded, retries
    not exhausted, and every dependency COMPLETED. Sorted by priority then id."""
    out = []
    for ms in [m for m in mission.ordered_milestones() if m.id == milestone_id]:
        for fid in ms.features:
            f = mission.features.get(fid)
            if not f:
                continue
            if f.status in (FeatureStatus.FAILED, FeatureStatus.NEEDS_FIX) and f.attempts >= f.max_attempts:
                continue  # exhausted retries
            if f.status in (FeatureStatus.PENDING, FeatureStatus.READY, FeatureStatus.NEEDS_FIX, FeatureStatus.FAILED):
                if all(_dep_ok(mission, d) for d in f.dependencies):
                    out.append(f)
    out.sort(key=lambda f: (f.priority, f.id))
    return out


def select_batch(ready: List[Feature], max_concurrent: int) -> List[Feature]:
    """Greedy: highest priority first; skip features that overlap an already
    selected member of this batch; stop at the concurrency ceiling."""
    batch: List[Feature] = []
    for f in ready:
        if len(batch) >= max_concurrent:
            break
        if any(_paths_overlap(f.expected_paths, b.expected_paths) for b in batch):
            continue
        batch.append(f)
    return batch


def find_cycle(features: Dict[str, Feature]) -> List[str]:
    """Return a dependency cycle (list of ids) if one exists, else []."""
    state: Dict[str, int] = {}
    stack: List[str] = []

    def dfs(node: str) -> List[str]:
        state[node] = 1
        stack.append(node)
        f = features.get(node)
        if f:
            for dep in f.dependencies:
                st = state.get(dep, 0)
                if st == 1:
                    return stack[stack.index(dep):] + [dep]
                if st == 0:
                    c = dfs(dep)
                    if c:
                        return c
        stack.pop()
        state[node] = 2
        return []

    for node in list(features):
        if state.get(node, 0) == 0:
            c = dfs(node)
            if c:
                return c
    return []
