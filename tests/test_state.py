import pytest

from hamgoose.models import FeatureStatus, MilestoneStatus, MissionStatus
from hamgoose.state import (
    IllegalTransition,
    feature_transition,
    milestone_transition,
    mission_transition,
)


def test_legal_run_pause_resume():
    mission_transition(MissionStatus.RUNNING, MissionStatus.PAUSED)
    mission_transition(MissionStatus.PAUSED, MissionStatus.RUNNING)


def test_cannot_resume_completed():
    with pytest.raises(IllegalTransition):
        mission_transition(MissionStatus.COMPLETED, MissionStatus.RUNNING)


def test_cannot_approve_from_running():
    with pytest.raises(IllegalTransition):
        mission_transition(MissionStatus.RUNNING, MissionStatus.AWAITING_APPROVAL)


def test_full_happy_path():
    s = MissionStatus.CREATED
    for nxt in (MissionStatus.ANALYZING, MissionStatus.PLANNING, MissionStatus.AWAITING_APPROVAL,
                MissionStatus.RUNNING, MissionStatus.VALIDATING, MissionStatus.COMPLETED):
        mission_transition(s, nxt)
        s = nxt


def test_feature_lifecycle():
    feature_transition(FeatureStatus.PENDING, FeatureStatus.READY)
    feature_transition(FeatureStatus.READY, FeatureStatus.RUNNING)
    feature_transition(FeatureStatus.RUNNING, FeatureStatus.COMPLETED)


def test_feature_no_completed_to_running():
    with pytest.raises(IllegalTransition):
        feature_transition(FeatureStatus.COMPLETED, FeatureStatus.RUNNING)


def test_milestone_flow():
    milestone_transition(MilestoneStatus.PENDING, MilestoneStatus.RUNNING)
    milestone_transition(MilestoneStatus.RUNNING, MilestoneStatus.VALIDATING)
    milestone_transition(MilestoneStatus.VALIDATING, MilestoneStatus.PASSED)
    with pytest.raises(IllegalTransition):
        milestone_transition(MilestoneStatus.PASSED, MilestoneStatus.RUNNING)
