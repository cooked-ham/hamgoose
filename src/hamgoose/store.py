"""Persistence layer.

Layout (under the target repository, git-ignored by default):

    <repo>/.goose/hamgoose/<mission-id>/
        mission.json     # canonical atomic state (single source of truth)
        mission.yaml     # human-readable mirror
        plan.md          # human-readable plan mirror
        events.jsonl     # append-only event log
        artifacts/       # worker raw outputs, diffs, logs
        workers/         # per-worker transcripts
        validation/      # per-validation reports

Persistence is atomic: the canonical JSON is written to a temp file in the same
directory and then os.replace()'d, so a crash mid-write cannot corrupt state.
Events are append-only.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import yaml

from .models import Mission, PlanRevision
from . import render


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


# -- paths ----------------------------------------------------------------- #
def hamgoose_root(repo: str) -> str:
    return os.path.join(os.path.abspath(repo), ".goose", "hamgoose")


def mission_dir(repo: str, mission_id: str) -> str:
    return os.path.join(hamgoose_root(repo), mission_id)


def mission_json(repo: str, mission_id: str) -> str:
    return os.path.join(mission_dir(repo, mission_id), "mission.json")


def events_path(repo: str, mission_id: str) -> str:
    return os.path.join(mission_dir(repo, mission_id), "events.jsonl")


def plan_path(repo: str, mission_id: str) -> str:
    return os.path.join(mission_dir(repo, mission_id), "plan.md")


def artifacts_dir(repo: str, mission_id: str) -> str:
    return os.path.join(mission_dir(repo, mission_id), "artifacts")


def workers_dir(repo: str, mission_id: str) -> str:
    return os.path.join(mission_dir(repo, mission_id), "workers")


def validation_dir(repo: str, mission_id: str) -> str:
    return os.path.join(mission_dir(repo, mission_id), "validation")


def ensure_dirs(repo: str, mission_id: str) -> None:
    for d in (
        mission_dir(repo, mission_id),
        artifacts_dir(repo, mission_id),
        workers_dir(repo, mission_id),
        validation_dir(repo, mission_id),
    ):
        os.makedirs(d, exist_ok=True)


# -- atomic IO ------------------------------------------------------------- #
def _atomic_write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


# -- mission load / save --------------------------------------------------- #
def load_mission(repo: str, mission_id: str) -> Optional[Mission]:
    path = mission_json(repo, mission_id)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        m = Mission.from_dict(json.load(f))
    # HG-11: events.jsonl is the canonical append-only log; the mission.events
    # list is a derived view hydrated on load (never persisted).
    m.events = read_events(repo, mission_id)
    return m


def _prev_state(repo: str, mission_id: str) -> Optional[Dict[str, Any]]:
    path = mission_json(repo, mission_id)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _plan_signature(d: Dict[str, Any]) -> tuple:
    feats = set((d.get("features") or {}).keys())
    mss = set((d.get("milestones") or {}).keys())
    return (frozenset(feats), frozenset(mss))


def save_mission(mission: Mission) -> None:
    ensure_dirs(mission.repo, mission.id)
    mission.updated_at = utcnow()
    prev = _prev_state(mission.repo, mission.id)

    # HG-10: structural plan changes must always land in plan_revisions. If the
    # feature/milestone id sets changed vs the last persisted state and no
    # existing revision covers the new structure, record who caught it (the
    # store) - direct writes are no longer invisible.
    if prev is not None and _plan_signature(prev) != _plan_signature(mission.to_dict()):
        covered = any(
            r.number == mission.current_revision
            and set(r.feature_ids) == set(mission.features.keys())
            and set(r.milestone_ids) == set(mission.milestones.keys())
            for r in mission.plan_revisions
        )
        if not covered:
            mission.current_revision += 1
            mission.plan_revisions.append(PlanRevision(
                number=mission.current_revision,
                created_at=utcnow(),
                note="external plan change (store)",
                feature_ids=list(mission.features.keys()),
                milestone_ids=list(mission.milestones.keys()),
            ))
            append_event(mission, "PLAN_REVISION_RECORDED", entity=mission.id,
                         payload={"number": mission.current_revision, "note": "external plan change (store)"})

    # HG-08: a hand-edited mission.json config block that is about to be
    # overwritten (or that disagrees with the controller's config) must be
    # visible, not silently lost.
    if prev is not None:
        prev_exec = (prev.get("config") or {}).get("execution") or {}
        new_exec = (mission.config or {}).get("execution") or {}
        if prev_exec and prev_exec != new_exec:
            changed = {k: {"was": prev_exec.get(k), "now": new_exec.get(k)}
                       for k in set(prev_exec) | set(new_exec) if prev_exec.get(k) != new_exec.get(k)}
            append_event(mission, "CONFIG_DRIFT", entity=mission.id, payload={"changed": changed})
        # The yaml mirror is human-readable but NEVER read back; a hand-edit
        # there is silently ignored - surface that too.
        try:
            ypath = os.path.join(mission_dir(mission.repo, mission.id), "mission.yaml")
            if os.path.exists(ypath):
                ydoc = yaml.safe_load(open(ypath, encoding="utf-8")) or {}
                y_exec = (ydoc.get("config") or {}).get("execution") or {}
                if y_exec and y_exec != new_exec:
                    changed = {k: {"yaml": y_exec.get(k), "now": new_exec.get(k)}
                               for k in set(y_exec) | set(new_exec) if y_exec.get(k) != new_exec.get(k)}
                    append_event(mission, "CONFIG_DRIFT", entity=mission.id,
                                 payload={"source": "mission.yaml", "changed": changed})
        except Exception:
            pass

    # HG-11: events are NEVER written here - events.jsonl is canonical.
    payload = mission.to_dict()
    payload.pop("events", None)
    _atomic_write(mission_json(mission.repo, mission.id), json.dumps(payload, indent=2))
    try:
        with open(os.path.join(mission_dir(mission.repo, mission.id), "mission.yaml"), "w", encoding="utf-8") as f:
            yaml.safe_dump(payload, f, sort_keys=False)
    except Exception:
        pass
    try:
        _atomic_write(plan_path(mission.repo, mission.id), render.plan_md(mission))
    except Exception:
        pass


def prune_commits(mission: Mission, feature_id: str, keep: "set") -> List[str]:
    """HG-12: curate a feature's commits list. Removes hashes not in `keep`
    (e.g. junk commits never reachable from the feature's branch tip) and
    returns the dropped hashes so the caller can record them in the event."""
    f = mission.features.get(feature_id)
    if not f:
        return []
    dropped = [c for c in f.commits if c not in keep]
    f.commits = [c for c in f.commits if c in keep]
    return dropped


def append_event(mission: Mission, etype: str, entity: Optional[str] = None, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    ev = {
        "ts": utcnow(),
        "mission_id": mission.id,
        "type": etype,
        "entity": entity,
        "payload": payload or {},
    }
    mission.events.append(ev)
    try:
        ensure_dirs(mission.repo, mission.id)
        with open(events_path(mission.repo, mission.id), "a", encoding="utf-8") as f:
            f.write(json.dumps(ev) + "\n")
            # H8: events.jsonl is the cross-process source of truth; a caller
            # polling mission_events must see an append immediately, so flush
            # to the OS (and fsync when the platform allows) on every event.
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
    except OSError:
        pass
    return ev


def read_events(repo: str, mission_id: str, tail: int = 0) -> List[Dict[str, Any]]:
    p = events_path(repo, mission_id)
    if not os.path.exists(p):
        return []
    lines = [l for l in open(p, encoding="utf-8").read().splitlines() if l.strip()]
    if tail:
        lines = lines[-tail:]
    return [json.loads(l) for l in lines]


def list_missions(repo: str) -> List[Dict[str, Any]]:
    root = hamgoose_root(repo)
    out = []
    if not os.path.isdir(root):
        return out
    from datetime import datetime, timezone as _tz

    now = datetime.now(_tz.utc)
    for name in sorted(os.listdir(root)):
        m = load_mission(repo, name)
        if m:
            done = sum(1 for f in m.features.values() if f.status.value == "COMPLETED")
            terminal = m.status.value in ("COMPLETED", "FAILED", "CANCELLED")
            age_days = None
            try:
                updated = datetime.fromisoformat(m.updated_at or "")
                age_days = max(0.0, (now - updated).total_seconds() / 86400.0)
            except Exception:
                pass
            out.append(
                {
                    "id": m.id,
                    "status": m.status.value,
                    "goal": m.goal,
                    "features": len(m.features),
                    "completed": done,
                    "updated_at": m.updated_at,
                    # H11: surface stale clutter so automation can filter or
                    # archive it instead of crossing missions.
                    "terminal": terminal,
                    "age_days": round(age_days, 2) if age_days is not None else None,
                    "stale": terminal or (age_days is not None and age_days >= 7.0),
                }
            )
    return out


def latest_mission(repo: str) -> Optional[Mission]:
    missions = [load_mission(repo, n) for n in os.listdir(hamgoose_root(repo))] if os.path.isdir(hamgoose_root(repo)) else []
    missions = [m for m in missions if m]
    if not missions:
        return None
    return max(missions, key=lambda m: m.updated_at or m.created_at)
