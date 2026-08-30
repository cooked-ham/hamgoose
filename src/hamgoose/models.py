"""Domain model for hamgoose missions.

Uses plain dataclasses so the orchestrator can freely mutate them during the
control loop. (De)serialization to/from plain dicts is handled here so the
persistence layer can stay storage-agnostic (JSON/YAML/SQLite).
"""
from __future__ import annotations

import re

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Optional


# --------------------------------------------------------------------------- #
# Enumerations
# --------------------------------------------------------------------------- #
class MissionStatus(str, Enum):
    CREATED = "CREATED"
    ANALYZING = "ANALYZING"
    PLANNING = "PLANNING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    BLOCKED = "BLOCKED"
    VALIDATING = "VALIDATING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class FeatureStatus(str, Enum):
    PENDING = "PENDING"
    READY = "READY"
    RUNNING = "RUNNING"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    NEEDS_FIX = "NEEDS_FIX"
    CANCELLED = "CANCELLED"
    SUPERSEDED = "SUPERSEDED"


class MilestoneStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    VALIDATING = "VALIDATING"
    PASSED = "PASSED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"


class FailureClass(str, Enum):
    MODEL_FAILURE = "MODEL_FAILURE"
    PROVIDER_FAILURE = "PROVIDER_FAILURE"
    WORKER_TIMEOUT = "WORKER_TIMEOUT"
    WORKER_CRASH = "WORKER_CRASH"
    IMPLEMENTATION_FAILURE = "IMPLEMENTATION_FAILURE"
    TEST_FAILURE = "TEST_FAILURE"
    VALIDATION_FAILURE = "VALIDATION_FAILURE"
    MERGE_CONFLICT = "MERGE_CONFLICT"
    DEPENDENCY_FAILURE = "DEPENDENCY_FAILURE"
    USER_BLOCKED = "USER_BLOCKED"
    INFRASTRUCTURE_FAILURE = "INFRASTRUCTURE_FAILURE"


#: Failure classes that are considered safely retryable.
RETRYABLE_FAILURES = {
    FailureClass.MODEL_FAILURE,
    FailureClass.PROVIDER_FAILURE,
    FailureClass.WORKER_TIMEOUT,
    FailureClass.WORKER_CRASH,
    FailureClass.IMPLEMENTATION_FAILURE,
    FailureClass.TEST_FAILURE,
}


# --------------------------------------------------------------------------- #
# Worker / result records
# --------------------------------------------------------------------------- #
@dataclass
class WorkerRecord:
    run_id: Optional[str] = None
    session_id: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    exit_code: Optional[int] = None
    backend: Optional[str] = None
    pid: Optional[int] = None


@dataclass
class FeatureResult:
    summary: Optional[str] = None
    changed_files: List[str] = field(default_factory=list)
    tests: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    raw: Optional[str] = None


@dataclass
class Finding:
    feature: str
    criterion: str
    problem: str
    evidence: str = ""
    recommended_fix: str = ""


@dataclass
class ValidationResult:
    kind: str  # "scrutiny" | "user_testing" | "final"
    passed: bool
    severity: str = "none"  # none | minor | major | critical
    findings: List[Finding] = field(default_factory=list)
    summary: str = ""
    raw: Optional[str] = None


# --------------------------------------------------------------------------- #
# Feature
# --------------------------------------------------------------------------- #
@dataclass
class Feature:
    id: str
    title: str
    description: str = ""
    milestone: str = ""
    dependencies: List[str] = field(default_factory=list)
    status: FeatureStatus = FeatureStatus.PENDING
    priority: int = 100
    acceptance_criteria: List[str] = field(default_factory=list)
    validation_commands: List[str] = field(default_factory=list)
    user_flows: List[str] = field(default_factory=list)
    validation_required: bool = True
    expected_paths: List[str] = field(default_factory=list)
    prohibited_paths: List[str] = field(default_factory=list)
    attempts: int = 0
    max_attempts: int = 3
    worker: WorkerRecord = field(default_factory=WorkerRecord)
    commits: List[str] = field(default_factory=list)
    branch: Optional[str] = None
    worktree: Optional[str] = None
    workdir: Optional[str] = None
    result: FeatureResult = field(default_factory=FeatureResult)
    fix_of: Optional[str] = None
    superseded_by: Optional[str] = None
    failure: Optional[str] = None
    failure_detail: str = ""
    is_fix: bool = False

    @property
    def is_terminal(self) -> bool:
        return self.status in {
            FeatureStatus.COMPLETED,
            FeatureStatus.FAILED,
            FeatureStatus.BLOCKED,
            FeatureStatus.CANCELLED,
            FeatureStatus.SUPERSEDED,
        }

    def summary_or(self, default: str) -> str:
        return (self.result.summary or default) if self.result else default

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        d["worker"] = asdict(self.worker)
        d["result"] = asdict(self.result)
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Feature":
        d = dict(d)
        d["status"] = FeatureStatus(d.get("status", "PENDING"))
        d["worker"] = WorkerRecord(**(d.get("worker") or {}))
        d["result"] = FeatureResult(**(d.get("result") or {}))
        d["failure"] = d.get("failure")
        return cls(**d)


# --------------------------------------------------------------------------- #
# Milestone
# --------------------------------------------------------------------------- #
@dataclass
class Milestone:
    id: str
    objective: str
    features: List[str] = field(default_factory=list)
    entry_requirements: List[str] = field(default_factory=list)
    completion_criteria: List[str] = field(default_factory=list)
    status: MilestoneStatus = MilestoneStatus.PENDING
    scrutiny_status: str = "pending"
    user_testing_status: str = "pending"
    validation: List[ValidationResult] = field(default_factory=list)
    correction_attempts: int = 0

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        d["validation"] = [asdict(v) for v in self.validation]
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Milestone":
        d = dict(d)
        d["status"] = MilestoneStatus(d.get("status", "PENDING"))
        d["validation"] = [
            ValidationResult(**{**v, "findings": [Finding(**f) for f in v.get("findings", [])]})
            for v in d.get("validation", [])
        ]
        return cls(**d)


# --------------------------------------------------------------------------- #
# Steering / plan revisions
# --------------------------------------------------------------------------- #
@dataclass
class SteeringNote:
    kind: str  # "steer" | "replan"
    instruction: str
    created_at: str
    applied: bool = False


@dataclass
class PlanRevision:
    number: int
    created_at: str
    note: str = ""
    feature_ids: List[str] = field(default_factory=list)
    milestone_ids: List[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Mission
# --------------------------------------------------------------------------- #
@dataclass
class Mission:
    id: str
    goal: str
    repo: str
    rules: Optional[str] = None
    status: MissionStatus = MissionStatus.CREATED
    created_at: str = ""
    updated_at: str = ""
    config: Dict[str, Any] = field(default_factory=dict)
    repo_analysis: Dict[str, Any] = field(default_factory=dict)
    plan_revisions: List[PlanRevision] = field(default_factory=list)
    current_revision: int = 1
    milestones: Dict[str, Milestone] = field(default_factory=dict)
    features: Dict[str, Feature] = field(default_factory=dict)
    events: List[Dict[str, Any]] = field(default_factory=list)
    steering: List[SteeringNote] = field(default_factory=list)
    active_milestone: Optional[str] = None
    base_commit: Optional[str] = None
    head_commit: Optional[str] = None
    worker_counter: int = 0
    feature_counter: int = 0
    milestone_counter: int = 0
    block_reason: str = ""
    pause_reason: str = ""
    readiness: Dict[str, Any] = field(default_factory=dict)
    final_validation: List[ValidationResult] = field(default_factory=list)
    correction_attempts: int = 0

    # -- convenience -------------------------------------------------------- #
    def feature(self, fid: str) -> Optional[Feature]:
        return self.features.get(fid)

    def ordered_milestones(self) -> List[Milestone]:
        return [self.milestones[k] for k in sorted(self.milestones.keys())]

    def milestone_features(self, mid: str) -> List[Feature]:
        ms = self.milestones.get(mid)
        if not ms:
            return []
        return [self.features[f] for f in ms.features if f in self.features]

    def next_feature_id(self) -> str:
        from .ids import feature_seq

        maxn = 0
        for fid in self.features:
            m = re.match(r"F(\d+)$", fid)
            if m:
                maxn = max(maxn, int(m.group(1)))
        self.feature_counter = max(self.feature_counter, maxn) + 1
        return feature_seq(self.feature_counter)

    def next_milestone_id(self) -> str:
        from .ids import milestone_id

        maxn = 0
        for mid in self.milestones:
            m = re.match(r"MS(\d+)$", mid)
            if m:
                maxn = max(maxn, int(m.group(1)))
        self.milestone_counter = max(self.milestone_counter, maxn) + 1
        return milestone_id(self.milestone_counter)

    # -- (de)serialization -------------------------------------------------- #
    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        d["milestones"] = {k: v.to_dict() for k, v in self.milestones.items()}
        d["features"] = {k: v.to_dict() for k, v in self.features.items()}
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Mission":
        d = dict(d)
        d["status"] = MissionStatus(d.get("status", "CREATED"))
        d["milestones"] = {k: Milestone.from_dict(v) for k, v in (d.get("milestones") or {}).items()}
        d["features"] = {k: Feature.from_dict(v) for k, v in (d.get("features") or {}).items()}
        d["steering"] = [SteeringNote(**s) for s in d.get("steering", [])]
        d["plan_revisions"] = [PlanRevision(**p) for p in d.get("plan_revisions", [])]
        d["final_validation"] = [
            ValidationResult(**{**v, "findings": [Finding(**f) for f in v.get("findings", [])]})
            for v in d.get("final_validation", [])
        ]
        return cls(**d)
