"""Validated state machines for missions, milestones and features.

Code enforces the legality of state transitions. Illegal transitions raise an
IllegalTransition error so the orchestrator can never corrupt mission state.
"""
from __future__ import annotations

from .models import FeatureStatus, MilestoneStatus, MissionStatus


class IllegalTransition(Exception):
    pass


MISSION_TRANSITIONS = {
    MissionStatus.CREATED: {MissionStatus.ANALYZING, MissionStatus.CANCELLED},
    MissionStatus.ANALYZING: {MissionStatus.PLANNING, MissionStatus.FAILED, MissionStatus.CANCELLED},
    MissionStatus.PLANNING: {MissionStatus.AWAITING_APPROVAL, MissionStatus.FAILED, MissionStatus.CANCELLED},
    MissionStatus.AWAITING_APPROVAL: {MissionStatus.RUNNING, MissionStatus.PLANNING, MissionStatus.CANCELLED, MissionStatus.FAILED},
    MissionStatus.RUNNING: {
        MissionStatus.PAUSED,
        MissionStatus.BLOCKED,
        MissionStatus.VALIDATING,
        MissionStatus.COMPLETED,
        MissionStatus.FAILED,
        MissionStatus.CANCELLED,
        MissionStatus.RUNNING,
    },
    MissionStatus.PAUSED: {MissionStatus.RUNNING, MissionStatus.CANCELLED, MissionStatus.FAILED},
    MissionStatus.BLOCKED: {MissionStatus.RUNNING, MissionStatus.CANCELLED, MissionStatus.FAILED},
    MissionStatus.VALIDATING: {
        MissionStatus.RUNNING,
        MissionStatus.COMPLETED,
        MissionStatus.FAILED,
        MissionStatus.BLOCKED,
        MissionStatus.PAUSED,
        MissionStatus.CANCELLED,
    },
    MissionStatus.COMPLETED: set(),
    MissionStatus.FAILED: set(),
    MissionStatus.CANCELLED: set(),
}

MILESTONE_TRANSITIONS = {
    MilestoneStatus.PENDING: {MilestoneStatus.RUNNING, MilestoneStatus.BLOCKED},
    MilestoneStatus.RUNNING: {MilestoneStatus.VALIDATING, MilestoneStatus.BLOCKED, MilestoneStatus.RUNNING},
    MilestoneStatus.VALIDATING: {MilestoneStatus.PASSED, MilestoneStatus.RUNNING, MilestoneStatus.FAILED, MilestoneStatus.BLOCKED},
    MilestoneStatus.PASSED: set(),
    MilestoneStatus.FAILED: set(),
    MilestoneStatus.BLOCKED: {MilestoneStatus.RUNNING, MilestoneStatus.FAILED},
}

FEATURE_TRANSITIONS = {
    FeatureStatus.PENDING: {FeatureStatus.READY, FeatureStatus.CANCELLED, FeatureStatus.SUPERSEDED, FeatureStatus.BLOCKED},
    FeatureStatus.READY: {FeatureStatus.RUNNING, FeatureStatus.CANCELLED, FeatureStatus.SUPERSEDED, FeatureStatus.BLOCKED},
    FeatureStatus.RUNNING: {
        FeatureStatus.VERIFYING,
        FeatureStatus.COMPLETED,
        FeatureStatus.FAILED,
        FeatureStatus.NEEDS_FIX,
        FeatureStatus.BLOCKED,
        FeatureStatus.READY,
    },
    FeatureStatus.VERIFYING: {
        FeatureStatus.COMPLETED,
        FeatureStatus.NEEDS_FIX,
        FeatureStatus.FAILED,
        FeatureStatus.BLOCKED,
    },
    FeatureStatus.NEEDS_FIX: {FeatureStatus.READY, FeatureStatus.BLOCKED, FeatureStatus.CANCELLED, FeatureStatus.SUPERSEDED},
    FeatureStatus.COMPLETED: {FeatureStatus.SUPERSEDED},
    FeatureStatus.FAILED: {FeatureStatus.READY, FeatureStatus.CANCELLED, FeatureStatus.BLOCKED, FeatureStatus.SUPERSEDED},
    FeatureStatus.BLOCKED: {FeatureStatus.READY, FeatureStatus.CANCELLED, FeatureStatus.SUPERSEDED},
    FeatureStatus.CANCELLED: set(),
    FeatureStatus.SUPERSEDED: set(),
}


def _check(table, frm, to, kind):
    allowed = table.get(frm, set())
    if to not in allowed:
        raise IllegalTransition(
            f"illegal {kind} transition: {getattr(frm, 'value', frm)} -> {getattr(to, 'value', to)}"
        )


def mission_transition(frm: MissionStatus, to: MissionStatus) -> None:
    _check(MISSION_TRANSITIONS, frm, to, "mission")


def milestone_transition(frm: MilestoneStatus, to: MilestoneStatus) -> None:
    _check(MILESTONE_TRANSITIONS, frm, to, "milestone")


def feature_transition(frm: FeatureStatus, to: FeatureStatus) -> None:
    _check(FEATURE_TRANSITIONS, frm, to, "feature")
