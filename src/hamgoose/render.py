"""Human-readable rendering of mission state (plans, mission control, etc.).

Kept separate from the persistence layer so store.py can emit mirrors without a
circular dependency.
"""
from __future__ import annotations

from typing import Any, Dict, List

from .models import FeatureStatus, Mission


def plan_md(mission: Mission) -> str:
    lines = ["# HAMGOOSE MISSION PLAN", "", f"Mission: {mission.id}", f"Goal: {mission.goal}"]
    if mission.rules:
        lines.append(f"Rules: {mission.rules}")
    lines.append("")
    lines.append(f"Plan revision: {mission.current_revision}")
    lines.append(f"Status: {mission.status.value}")
    lines.append("")
    for ms in mission.ordered_milestones():
        lines.append(f"## {ms.id} - {ms.objective} ({ms.status.value})")
        for fid in ms.features:
            f = mission.features.get(fid)
            if not f:
                continue
            deps = f", ".join(f.dependencies) if f.dependencies else "-"
            lines.append(f"  - {f.id} {f.title}  [status={f.status.value}, deps={deps}]")
            for ac in f.acceptance_criteria:
                lines.append(f"      - {ac}")
        lines.append("")
    lines.append("Validation:")
    v = mission.config.get("validation", {}) if isinstance(mission.config, dict) else {}
    lines.append(f"  Scrutiny validation: {'enabled' if v.get('scrutiny', True) else 'disabled'}")
    lines.append(f"  User-facing validation: {'enabled' if v.get('user_testing', True) else 'disabled'}")
    lines.append("")
    n_features = len(mission.features)
    est_workers = sum(f.max_attempts for f in mission.features.values())
    max_corrections = v.get("max_correction_attempts", 3)
    lines.append(f"Estimated worker runs: <= {est_workers}")
    lines.append(f"Estimated validation runs: <= {len(mission.ordered_milestones()) * 2 * max_corrections}")
    lines.append(f"Features: {n_features}")
    return "\n".join(lines)


def mission_control(mission: Mission) -> str:
    lines = ["HAMGOOSE MISSION CONTROL", ""]
    lines.append(f"Mission: {mission.id}")
    lines.append(f"Goal: {mission.goal}")
    if mission.rules:
        lines.append(f"Rules: {mission.rules}")
    lines.append(f"Status: {mission.status.value}")
    if mission.pause_reason:
        lines.append(f"Pause reason: {mission.pause_reason}")
    if mission.block_reason:
        lines.append(f"Blocked: {mission.block_reason}")
    lines.append("")

    milestones = mission.ordered_milestones()
    active_idx = next((i for i, m in enumerate(milestones) if m.id == mission.active_milestone), -1)
    if milestones:
        lines.append(f"Milestone {max(active_idx + 1, 1)}/{len(milestones)}")
        for i, ms in enumerate(milestones):
            feats = [mission.features[f] for f in ms.features if f in mission.features]
            done = sum(1 for f in feats if f.status.value == "COMPLETED")
            mark = "*" if ms.id == mission.active_milestone else " "
            lines.append(f"{mark} {ms.id} - {ms.objective}  [{done}/{len(feats)} features, {ms.status.value}]")
    lines.append("")

    # Feature breakdown for active (or first non-passed) milestone
    ms = mission.milestones.get(mission.active_milestone) or (milestones[0] if milestones else None)
    if ms:
        buckets: Dict[str, List] = {}
        for f in [mission.features[fid] for fid in ms.features if fid in mission.features]:
            buckets.setdefault(f.status.value, []).append(f)
        order = ["RUNNING", "VERIFYING", "READY", "NEEDS_FIX", "BLOCKED", "FAILED", "PENDING", "COMPLETED"]
        for st in order:
            if st not in buckets:
                continue
            lines.append(st)
            items = buckets[st]
            if st == "COMPLETED":
                lines.append("  " + "  ".join(f.id for f in items))
            else:
                for f in items:
                    dep = f" <- {'|'.join(f.dependencies)}" if f.dependencies else ""
                    lines.append(f"  {f.id} {f.title}{dep}")
            lines.append("")

    # Workers
    running = [f for f in mission.features.values() if f.status.value in ("RUNNING", "VERIFYING") and f.worker.run_id]
    if running:
        lines.append("Workers")
        for f in running:
            lines.append(f"  {f.worker.run_id}  {f.id}  {f.status.value.lower()}")
        lines.append("")

    # Validation
    vals = [(ms.id, ms) for ms in milestones if ms.validation]
    if vals:
        lines.append("Validation")
        for mid, m in vals:
            last = m.validation[-1]
            lines.append(f"  Milestone {mid}: {last.kind} {'PASSED' if last.passed else 'FAILED'}")
        lines.append("")

    if mission.events:
        lines.append("Recent events")
        for ev in mission.events[-6:]:
            lines.append(f"  {ev.get('ts','')[:19]}  {ev.get('type','')}  {ev.get('entity') or ''}".rstrip())
    return "\n".join(lines)


def readiness_md(report: Dict[str, Any]) -> str:
    lines = ["HAMGOOSE READINESS", ""]
    for name in report.get("checks", []):
        lines.append(f"  {name:<22} {report.get(name, 'N/A')}")
    lines.append("")
    warn = [n for n in report.get("checks", []) if report.get(n) == "WARN"]
    fail = [n for n in report.get("checks", []) if report.get(n) == "FAIL"]
    lines.append(f"Warnings: {len(warn)}   Failures: {len(fail)}")
    if report.get("notes"):
        for n in report["notes"]:
            lines.append(f"  - {n}")
    return "\n".join(lines)
