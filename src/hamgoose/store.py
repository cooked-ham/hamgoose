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

from .models import Mission
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
        return Mission.from_dict(json.load(f))


def save_mission(mission: Mission) -> None:
    ensure_dirs(mission.repo, mission.id)
    mission.updated_at = utcnow()
    _atomic_write(mission_json(mission.repo, mission.id), json.dumps(mission.to_dict(), indent=2))
    try:
        with open(os.path.join(mission_dir(mission.repo, mission.id), "mission.yaml"), "w", encoding="utf-8") as f:
            yaml.safe_dump(mission.to_dict(), f, sort_keys=False)
    except Exception:
        pass
    try:
        _atomic_write(plan_path(mission.repo, mission.id), render.plan_md(mission))
    except Exception:
        pass


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
    for name in sorted(os.listdir(root)):
        m = load_mission(repo, name)
        if m:
            done = sum(1 for f in m.features.values() if f.status.value == "COMPLETED")
            out.append(
                {
                    "id": m.id,
                    "status": m.status.value,
                    "goal": m.goal,
                    "features": len(m.features),
                    "completed": done,
                    "updated_at": m.updated_at,
                }
            )
    return out


def latest_mission(repo: str) -> Optional[Mission]:
    missions = [load_mission(repo, n) for n in os.listdir(hamgoose_root(repo))] if os.path.isdir(hamgoose_root(repo)) else []
    missions = [m for m in missions if m]
    if not missions:
        return None
    return max(missions, key=lambda m: m.updated_at or m.created_at)
