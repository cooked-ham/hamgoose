"""The Mission orchestrator / controller.

Owns the deterministic control loop: scheduling, worker dispatch, reconciliation,
validation, corrective work, retries, state transitions, persistence and crash
recovery. Semantic decisions (decomposition, diagnosis, replanning, validation
verdicts) are delegated to isolated Goose contexts via the semantic/worker/
validation backends.

Core principle: prompts express semantic intent; THIS code enforces orchestration
mechanics and never trusts a worker's self-report.
"""
from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional

from . import plan as planmod
from . import prompting, redact, render, scheduler, store
from .config import Config
from .git import GitManager
from .ids import fix_id, mission_id
from .models import (
    Feature,
    FeatureResult,
    FeatureStatus,
    Finding,
    FailureClass,
    Mission,
    MissionStatus,
    Milestone,
    MilestoneStatus,
    PlanRevision,
    RETRYABLE_FAILURES,
    SteeringNote,
    ValidationResult,
)
from .semantic import SemanticClient, extract_json
from .state import feature_transition, milestone_transition, mission_transition
from .validator import ValidationBackend
from .worker import WorkerBackend, WorkerResult, _default_simulator


def _deep_merge(base: Dict[str, Any], override: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    out = dict(base or {})
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


class MissionController:
    def __init__(
        self,
        repo: str,
        config: Optional[Config] = None,
        worker_backend: Optional[WorkerBackend] = None,
        validation_backend: Optional[ValidationBackend] = None,
        semantic: Optional[SemanticClient] = None,
        planner: Optional[Callable[[Mission, str], Dict[str, Any]]] = None,
        project_context: Optional[str] = None,
    ):
        self.repo = os.path.abspath(repo)
        self.config = config or Config.load()
        self.worker_backend = worker_backend
        self.validation_backend = validation_backend
        self.semantic = semantic or SemanticClient(self.config)
        self.planner = planner  # optional deterministic planner for tests: (mission, goal)->plan dict
        self._project_context_override = project_context
        self._progress_cb = None  # optional callback(msg, current, total) for MCP progress notifications
        if self.worker_backend is None:
            from .worker import GooseRunBackend

            self.worker_backend = GooseRunBackend()
        if self.validation_backend is None:
            from .validator import GooseRunValidationBackend

            self.validation_backend = GooseRunValidationBackend(self.semantic)

    # ------------------------------------------------------------------ #
    # helpers
    # ------------------------------------------------------------------ #
    def _cfg(self, m: Mission) -> Config:
        if m.config:
            try:
                return Config.load(m.config)
            except Exception:
                pass
        return self.config

    # ------------------------------------------------------------------ #
    # progress reporting (used by server.py -> MCP progress notifications)
    # ------------------------------------------------------------------ #
    def set_progress(self, cb) -> None:
        """Attach a progress callback (message, current, total). Safe to call
        before a long-running operation; always safe to leave attached."""
        self._progress_cb = cb

    def _report(self, message: str, current: float, total: float) -> None:
        if self._progress_cb is not None:
            try:
                self._progress_cb(message, current, total)
            except Exception:
                pass

    def _run_status_line(self, m: Mission) -> str:
        """One-line progress summary used to keep the user informed between steps."""
        ms = self._active_milestone(m)
        if ms is None:
            return "final validation"
        feats = [f for f in m.milestone_features(ms.id) if f.status != FeatureStatus.SUPERSEDED]
        done = sum(1 for f in feats if f.is_terminal)
        total = len(feats)
        running = [f.id for f in feats if f.status == FeatureStatus.RUNNING]
        line = "{} ({}) {}/{} features done".format(ms.id, ms.objective[:40] or "milestone", done, total)
        if running:
            line += " · working: " + ", ".join(running)
        return line

    def _project_context(self, m: Mission) -> str:
        if self._project_context_override is not None:
            return self._project_context_override
        if m.repo_analysis.get("instructions"):
            return str(m.repo_analysis.get("instructions"))
        return ""

    # ------------------------------------------------------------------ #
    # lifecycle: create / analyze / plan / approve
    # ------------------------------------------------------------------ #
    def create_mission(
        self,
        goal: str,
        config_overrides: Optional[Dict[str, Any]] = None,
        rules: Optional[str] = None,
    ) -> Mission:
        # Base the mission config on the controller's config, then apply any
        # per-mission overrides (so e.g. a worker max_turns set on the controller
        # is honored).
        cfg = Config(**_deep_merge(self.config.to_dict(), config_overrides or {}))
        m = Mission(
            id=mission_id(),
            goal=goal,
            repo=self.repo,
            rules=rules or None,
            status=MissionStatus.CREATED,
            config=cfg.to_dict(),
        )
        m.created_at = store.utcnow()
        store.save_mission(m)
        store.append_event(m, "MISSION_CREATED", entity=m.id, payload={"goal": goal, "rules": rules or ""})

        mission_transition(m.status, MissionStatus.ANALYZING)
        m.status = MissionStatus.ANALYZING
        self._analyze(m)
        mission_transition(m.status, MissionStatus.PLANNING)
        m.status = MissionStatus.PLANNING
        store.save_mission(m)
        return m

    def _analyze(self, m: Mission) -> None:
        gm = GitManager(self.repo)
        status = gm.status()
        m.base_commit = status.get("base_commit")
        m.readiness = planmod.check_readiness(self.repo)
        instructions = self._discover_instructions()
        summary = self._repo_summary()
        m.repo_analysis = {"git": status, "instructions": instructions, "summary": summary}
        store.append_event(m, "REPOSITORY_ANALYZED", entity=m.id, payload={"summary_len": len(summary)})
        store.save_mission(m)

    def _discover_instructions(self) -> str:
        parts = []
        for name in ("AGENTS.md", ".goosehints", "CLAUDE.md", "CONTRIBUTING.md", "README.md"):
            p = os.path.join(self.repo, name)
            if os.path.exists(p):
                try:
                    parts.append("### {}\n{}".format(name, open(p, encoding="utf-8", errors="ignore").read()[:4000]))
                except OSError:
                    pass
        return "\n\n".join(parts)[:12000]

    def _repo_summary(self) -> str:
        try:
            entries = sorted(os.listdir(self.repo))[:80]
        except OSError:
            entries = []
        return "top-level entries: " + ", ".join(entries)

    def plan(
        self,
        mission_id_: str,
        features: Optional[List[Dict[str, Any]]] = None,
        milestones: Optional[List[Dict[str, Any]]] = None,
    ) -> Mission:
        m = self._get(mission_id_)
        if m.status not in (MissionStatus.PLANNING, MissionStatus.CREATED):
            raise ValueError("cannot plan a mission in state {}".format(m.status.value))

        if features is None:
            self._report("decomposing mission plan (isolated goose run)", 30, 100)
            plan_dict = self._generate_plan(m)
            self._report("plan generated; validating structure", 80, 100)
        else:
            plan_dict = {"milestones": milestones or [], "features": features}
            self._report("validating plan structure", 80, 100)
        if not plan_dict.get("features"):
            # Fail loudly instead of presenting an empty plan for approval:
            # a 0-feature plan is a planner failure, not a plan.
            m.repo_analysis["plan_error"] = "planner returned no features (goal may be too vague for the repo analysis)"
            store.append_event(m, "PLAN_FAILED", entity=m.id, payload={"reason": "no features"})
            store.save_mission(m)
            raise ValueError(
                "empty plan (0 features): the goal could not be decomposed from the "
                "repository analysis. Ask the user for a more concrete goal (specific "
                "files, behaviors, or docs to continue), then call mission_plan again "
                "- or pass your own decomposition via the features/milestones params."
            )

        self._apply_plan(m, plan_dict, note="initial plan", revision=1 if not m.plan_revisions else m.current_revision + 1)
        mission_transition(m.status, MissionStatus.AWAITING_APPROVAL)
        m.status = MissionStatus.AWAITING_APPROVAL
        store.append_event(m, "PLAN_GENERATED", entity=m.id, payload={"features": len(m.features), "milestones": len(m.milestones)})
        store.save_mission(m)
        self._report("plan ready for approval", 100, 100)
        return m

    def _generate_plan(self, m: Mission) -> Dict[str, Any]:
        if self.planner is not None:
            return self.planner(m, m.goal)
        max_features = 12
        prompt = prompting.decompose_prompt(m.goal, m.repo_analysis.get("summary", ""), self._project_context(m), max_features)
        text = self.semantic.complete(prompt, role="orchestrator")
        data = extract_json(text) or {}
        if not data.get("features"):
            # One grounded retry: a vague goal + a weak first response should not
            # silently become an empty plan (plan() rejects those).
            retry = prompt + (
                "\n\nIMPORTANT: Your previous response contained no usable features. "
                "Decompose the goal NOW. Ground it in the REPOSITORY ANALYSIS above and "
                "in any existing plan/progress docs it mentions; if the goal references "
                "prior work, derive concrete steps from the repository state. You MUST "
                "return at least one milestone with at least one feature - an empty "
                "plan is a failure."
            )
            data = extract_json(self.semantic.complete(retry, role="orchestrator")) or data
        return data

    def _apply_plan(self, m: Mission, plan_dict: Dict[str, Any], note: str, revision: int) -> None:
        cfg = self._cfg(m)
        # milestones
        for msd in plan_dict.get("milestones", []) or []:
            mid = msd.get("id") or m.next_milestone_id()
            ms = Milestone(
                id=mid,
                objective=str(msd.get("objective", "")),
                completion_criteria=list(msd.get("completion_criteria", []) or []),
            )
            m.milestones[mid] = ms
        # features
        for f in plan_dict.get("features", []) or []:
            fid = f.get("id") or m.next_feature_id()
            ft = Feature(
                id=fid,
                title=str(f.get("title", "")),
                description=str(f.get("description", "")),
                milestone=str(f.get("milestone", m.ordered_milestones()[0].id if m.ordered_milestones() else "")),
                dependencies=list(f.get("dependencies", []) or []),
                priority=int(f.get("priority", 100)),
                acceptance_criteria=list(f.get("acceptance_criteria", []) or []),
                validation_commands=list(f.get("validation_commands", []) or []),
                user_flows=list(f.get("user_flows", []) or []),
                expected_paths=list(f.get("expected_paths", []) or []),
                prohibited_paths=list(f.get("prohibited_paths", []) or []),
                validation_required=bool(f.get("validation_required", True)),
                max_attempts=int(f.get("max_attempts", cfg.execution.max_feature_attempts)),
            )
            m.features[fid] = ft
        # link features to milestones
        for fid, ft in m.features.items():
            if ft.milestone in m.milestones and fid not in m.milestones[ft.milestone].features:
                m.milestones[ft.milestone].features.append(fid)

        # self-validate and best-effort fix before approval
        issues = planmod.validate_plan(m)
        fixed = planmod.fix_plan(m)
        m.plan_revisions.append(PlanRevision(
            number=revision, created_at=store.utcnow(), note=note,
            feature_ids=list(m.features.keys()), milestone_ids=list(m.milestones.keys()),
        ))
        m.current_revision = revision
        m.repo_analysis["plan_issues"] = issues
        m.repo_analysis["plan_fixes"] = fixed
        if m.active_milestone is None and m.milestones:
            m.active_milestone = m.ordered_milestones()[0].id

    def approve(self, mission_id_: str) -> Mission:
        m = self._get(mission_id_)
        if m.status != MissionStatus.AWAITING_APPROVAL:
            raise ValueError("mission is not awaiting approval (state={})".format(m.status.value))
        if not m.features:
            raise ValueError("cannot approve an empty plan (0 features); generate one with mission_plan first")
        mission_transition(m.status, MissionStatus.RUNNING)
        m.status = MissionStatus.RUNNING
        store.append_event(m, "PLAN_APPROVED", entity=m.id)
        self._setup_base(m)
        if m.active_milestone in m.milestones and m.milestones[m.active_milestone].status == MilestoneStatus.PENDING:
            milestone_transition(m.milestones[m.active_milestone].status, MilestoneStatus.RUNNING)
            m.milestones[m.active_milestone].status = MilestoneStatus.RUNNING
            store.append_event(m, "MILESTONE_STARTED", entity=m.active_milestone)
        store.save_mission(m)
        return m

    # ------------------------------------------------------------------ #
    # run loop
    # ------------------------------------------------------------------ #
    def run(self, mission_id_: str, max_steps: Optional[int] = None) -> str:
        m = self._get(mission_id_)
        if m.status == MissionStatus.COMPLETED:
            return render.mission_control(m) + "\n\n(mission already completed)"
        if m.status == MissionStatus.CANCELLED or m.status == MissionStatus.FAILED:
            return render.mission_control(m) + "\n\n(mission is terminal)"
        if m.status == MissionStatus.PAUSED:
            return "Mission paused ({})\n\n{}".format(m.pause_reason or "no reason", render.mission_control(m))
        if m.status == MissionStatus.BLOCKED:
            return "Mission BLOCKED: {}\n(steer/retry/replan to continue)\n\n{}".format(m.block_reason, render.mission_control(m))
        if m.status not in (MissionStatus.RUNNING, MissionStatus.VALIDATING):
            return "Mission is in state {} - approve it first.\n\n{}".format(m.status.value, render.mission_control(m))

        self._reconcile(m)
        cfg = self._cfg(m)
        steps = max_steps or cfg.execution.max_steps_per_run
        for i in range(steps):
            self._report(self._run_status_line(m), i + 1, steps)
            stable = self._step(m)
            store.save_mission(m)
            if stable:
                break
        self._report(self._run_status_line(m), steps, steps)
        return self._run_summary(m)

    def _run_summary(self, m: Mission) -> str:
        out = render.mission_control(m)
        if m.status == MissionStatus.RUNNING and not _all_done(m):
            out += "\n\n(in progress - call mission_run again to continue)"
        return out

    def _step(self, m: Mission) -> bool:
        self._reconcile(m)
        ms = self._active_milestone(m)
        if ms is None:
            return self._final_phase(m)

        if ms.status == MilestoneStatus.PENDING:
            milestone_transition(ms.status, MilestoneStatus.RUNNING)
            ms.status = MilestoneStatus.RUNNING
            store.append_event(m, "MILESTONE_STARTED", entity=ms.id)

        feats = [f for f in m.milestone_features(ms.id) if f.status != FeatureStatus.SUPERSEDED]
        all_terminal = len(feats) > 0 and all(f.is_terminal for f in feats)

        if all_terminal:
            stuck = [f for f in feats if f.status in (FeatureStatus.FAILED, FeatureStatus.BLOCKED)]
            if stuck:
                self._set_blocked(m, ms, stuck)
                return True
            return self._validate_milestone(m, ms)

        ready = scheduler.ready_features(m, ms.id)
        if not ready:
            if scheduler.find_cycle(m.features):
                m.block_reason = "dependency cycle in feature graph"
                self._transition_mission(m, MissionStatus.BLOCKED)
                store.append_event(m, "MISSION_BLOCKED", entity=m.id, payload={"reason": m.block_reason})
                return True
            exhausted = [f for f in feats if f.status in (FeatureStatus.FAILED, FeatureStatus.BLOCKED)]
            if exhausted:
                self._set_blocked(m, ms, exhausted)
                return True
            return True

        cfg = self._cfg(m)
        batch = scheduler.select_batch(ready, cfg.execution.max_concurrent_workers)
        store.append_event(m, "FEATURE_READY", entity=",".join(f.id for f in batch))
        self._dispatch_batch(m, batch, cfg)
        return False

    # ------------------------------------------------------------------ #
    # milestone validation + corrective loop
    # ------------------------------------------------------------------ #
    def _validate_milestone(self, m: Mission, ms: Milestone) -> bool:
        cfg = self._cfg(m)
        milestone_transition(ms.status, MilestoneStatus.VALIDATING)
        ms.status = MilestoneStatus.VALIDATING
        self._transition_mission(m, MissionStatus.VALIDATING)
        store.append_event(m, "VALIDATION_STARTED", entity=ms.id)

        base, head = self._base_head(m)
        ctx = self._project_context(m)
        passed = True
        checks = []
        if cfg.validation.scrutiny:
            checks.append("scrutiny")
        if cfg.validation.user_testing:
            checks.append("user_testing")

        # These validators inspect the same immutable revision independently;
        # running them together cuts validation wall time roughly in half while
        # preserving both required verdicts and their stable output order.
        results = []
        if checks:
            with ThreadPoolExecutor(max_workers=len(checks)) as executor:
                futures = [executor.submit(
                    self.validation_backend.run,
                    kind, m, ms.id, base, head, self._validation_workdir(m), ctx,
                ) for kind in checks]
                results = [future.result() for future in futures]
        for r in results:
            if r.kind == "scrutiny":
                ms.scrutiny_status = "passed" if r.passed else "failed"
            elif r.kind == "user_testing":
                ms.user_testing_status = "passed" if r.passed else "failed"
        for r in results:
            ms.validation.append(r)
            if not r.passed:
                passed = False
            self._save_validation(m, ms.id, r)

        if passed:
            milestone_transition(ms.status, MilestoneStatus.PASSED)
            ms.status = MilestoneStatus.PASSED
            store.append_event(m, "VALIDATION_PASSED", entity=ms.id)
            store.append_event(m, "MILESTONE_COMPLETED", entity=ms.id)
            # advance active milestone
            self._advance_milestone(m)
            self._transition_mission(m, MissionStatus.RUNNING)
            store.save_mission(m)
            return False  # continue to next milestone / final phase
        else:
            # corrective work
            m.correction_attempts += 1
            ms.correction_attempts += 1
            self._create_fix_features(m, ms, results)
            if ms.correction_attempts >= cfg.validation.max_correction_attempts:
                milestone_transition(ms.status, MilestoneStatus.BLOCKED)
                ms.status = MilestoneStatus.BLOCKED
                m.block_reason = "milestone {} failed required validation after {} corrections".format(ms.id, ms.correction_attempts)
                self._transition_mission(m, MissionStatus.BLOCKED)
                store.append_event(m, "MISSION_BLOCKED", entity=m.id, payload={"reason": m.block_reason})
                store.save_mission(m)
                return True
            milestone_transition(MilestoneStatus.VALIDATING, MilestoneStatus.RUNNING)
            ms.status = MilestoneStatus.RUNNING
            store.append_event(m, "VALIDATION_FAILED", entity=ms.id)
            self._transition_mission(m, MissionStatus.RUNNING)
            store.save_mission(m)
            return False

    def _create_fix_features(self, m: Mission, ms: Milestone, results: List[ValidationResult]) -> None:
        cfg = self._cfg(m)
        seen = set()
        for r in results:
            for f in r.findings:
                target = f.feature if f.feature in m.features and f.feature != "milestone" else None
                if target:
                    key = (target, f.criterion)
                else:
                    # attribute to the milestone's first non-fix feature
                    cand = next((x for x in ms.features if x in m.features), None)
                    key = (cand, f.criterion)
                if not key[0] or key in seen:
                    continue
                seen.add(key)
                src = m.features[key[0]]
                new_id = fix_id(src.id, src.attempts + 1)
                if new_id in m.features:
                    continue
                ft = Feature(
                    id=new_id,
                    title="FIX: " + (f.problem or f.criterion)[:120],
                    description="Corrective work for {}.\nProblem: {}\nFix: {}\nPrior evidence: {}".format(
                        src.id, f.problem, f.recommended_fix, f.evidence
                    ),
                    milestone=ms.id,
                    dependencies=[d for d in src.dependencies if d in m.features],
                    priority=10,
                    acceptance_criteria=["Defect fixed: {}".format(f.problem), *(src.acceptance_criteria)],
                    expected_paths=src.expected_paths,
                    prohibited_paths=src.prohibited_paths,
                    validation_required=True,
                    max_attempts=cfg.execution.max_feature_attempts,
                    fix_of=src.id,
                    is_fix=True,
                )
                m.features[new_id] = ft
                if new_id not in ms.features:
                    ms.features.append(new_id)
                store.append_event(m, "FIX_FEATURE_CREATED", entity=new_id, payload={"for": src.id})
                src.failure_detail = (src.failure_detail or "") + "\n[fix {} created]".format(new_id)

    def _final_phase(self, m: Mission) -> bool:
        # all milestones passed -> final validation gate
        already = m.final_validation and m.final_validation[-1].passed
        if already:
            self._transition_mission(m, MissionStatus.COMPLETED)
            store.append_event(m, "MISSION_COMPLETED", entity=m.id)
            store.save_mission(m)
            return True
        self._transition_mission(m, MissionStatus.VALIDATING)
        base, head = self._base_head(m)
        r = self.validation_backend.run("final", m, "", base, head, self._validation_workdir(m), self._project_context(m))
        m.final_validation.append(r)
        self._save_validation(m, "final", r)
        if r.passed:
            self._transition_mission(m, MissionStatus.COMPLETED)
            store.append_event(m, "VALIDATION_PASSED", entity="final")
            store.append_event(m, "MISSION_COMPLETED", entity=m.id)
            store.save_mission(m)
            return True
        # corrective work on a NEW corrective milestone (a PASSED milestone is
        # terminal and must not be re-opened)
        m.correction_attempts += 1
        cfg = self._cfg(m)
        if m.correction_attempts >= cfg.validation.max_correction_attempts:
            m.block_reason = "final validation failed after {} corrections".format(m.correction_attempts)
            self._transition_mission(m, MissionStatus.BLOCKED)
            store.append_event(m, "MISSION_BLOCKED", entity=m.id, payload={"reason": m.block_reason})
            store.save_mission(m)
            return True
        corr_id = m.next_milestone_id()
        corr = Milestone(id=corr_id, objective="Final corrections", status=MilestoneStatus.PENDING, features=[])
        m.milestones[corr_id] = corr
        self._create_final_fixes(m, corr, r)
        m.active_milestone = corr_id
        self._transition_mission(m, MissionStatus.RUNNING)
        store.append_event(m, "VALIDATION_FAILED", entity="final")
        store.append_event(m, "MILESTONE_STARTED", entity=corr_id)
        store.save_mission(m)
        return False

    def _create_final_fixes(self, m: Mission, corr: Milestone, r: ValidationResult) -> None:
        cfg = self._cfg(m)
        seen = set()
        for f in r.findings:
            target = f.feature if f.feature in m.features and f.feature not in ("-", "milestone") else None
            if target:
                src = m.features[target]
                nid = fix_id(src.id, src.attempts + 1)
                if nid in m.features:
                    nid = m.next_feature_id()
                ft = Feature(
                    id=nid, title="FIX: " + (f.problem or f.criterion)[:120],
                    description="Final corrective work for {}.\nProblem: {}\nFix: {}".format(target, f.problem, f.recommended_fix),
                    milestone=corr.id, dependencies=[d for d in src.dependencies if d in m.features],
                    priority=10, acceptance_criteria=["Defect fixed: {}".format(f.problem)],
                    expected_paths=src.expected_paths, prohibited_paths=src.prohibited_paths,
                    validation_required=True, max_attempts=cfg.execution.max_feature_attempts,
                    fix_of=target, is_fix=True,
                )
            else:
                nid = m.next_feature_id()
                ft = Feature(
                    id=nid, title="FIX: " + (f.problem or f.criterion)[:120],
                    description="Final corrective work.\nProblem: {}\nFix: {}".format(f.problem, f.recommended_fix),
                    milestone=corr.id, priority=10,
                    acceptance_criteria=["Defect fixed: {}".format(f.problem)], validation_required=True,
                    max_attempts=cfg.execution.max_feature_attempts, is_fix=True,
                )
            m.features[nid] = ft
            if nid not in corr.features:
                corr.features.append(nid)
            store.append_event(m, "FIX_FEATURE_CREATED", entity=nid, payload={"for": f.feature})

    def _set_blocked(self, m: Mission, ms: Milestone, stuck: List[Feature]) -> None:
        reasons = "; ".join("{}:{}".format(f.id, f.failure or f.status.value) for f in stuck)
        milestone_transition(ms.status, MilestoneStatus.BLOCKED)
        ms.status = MilestoneStatus.BLOCKED
        m.block_reason = "milestone {} blocked: {}".format(ms.id, reasons)
        self._transition_mission(m, MissionStatus.BLOCKED)
        store.append_event(m, "MISSION_BLOCKED", entity=m.id, payload={"reason": m.block_reason, "features": [f.id for f in stuck]})
        store.save_mission(m)

    def _active_milestone(self, m: Mission) -> Optional[Milestone]:
        if m.active_milestone and m.active_milestone in m.milestones:
            ms = m.milestones[m.active_milestone]
            if ms.status != MilestoneStatus.PASSED:
                return ms
        for ms in m.ordered_milestones():
            if ms.status != MilestoneStatus.PASSED:
                return ms
        return None

    def _advance_milestone(self, m: Mission) -> None:
        for ms in m.ordered_milestones():
            if ms.status != MilestoneStatus.PASSED:
                m.active_milestone = ms.id
                return
        m.active_milestone = None

    # ------------------------------------------------------------------ #
    # dispatch / reconcile
    # ------------------------------------------------------------------ #
    def _dispatch_batch(self, m: Mission, batch: List[Feature], cfg: Config) -> None:
        role = cfg.resolved_worker()
        for f in batch:
            self._to_running(f)
            f.worker.started_at = store.utcnow()
            f.workdir = self._workdir_for(f, m)
            store.append_event(m, "WORKER_STARTED", entity=f.id, payload={"backend": self.worker_backend.name})
        store.save_mission(m)

        def _launch(f: Feature) -> WorkerResult:
            prompt = prompting.worker_prompt(m, f, self._git_info(f, m), self._project_context(m))
            return self.worker_backend.run(prompt, f.workdir or self.repo, role, f, cfg.execution.worker_timeout)

        if len(batch) == 1:
            results = {batch[0].id: _launch(batch[0])}
        else:
            with ThreadPoolExecutor(max_workers=len(batch)) as ex:
                futs = {f.id: ex.submit(_launch, f) for f in batch}
                results = {fid: fut.result() for fid, fut in futs.items()}

        for f in batch:
            res = results[f.id]
            self._reconcile_result(m, f, res, cfg)
            store.save_mission(m)

    def _to_running(self, f: Feature) -> None:
        s = f.status
        if s == FeatureStatus.PENDING:
            feature_transition(s, FeatureStatus.READY)
            f.status = FeatureStatus.READY
        if f.status in (FeatureStatus.READY,):
            feature_transition(f.status, FeatureStatus.RUNNING)
        elif f.status == FeatureStatus.NEEDS_FIX:
            feature_transition(f.status, FeatureStatus.READY)
            f.status = FeatureStatus.READY
            feature_transition(f.status, FeatureStatus.RUNNING)
        elif f.status in (FeatureStatus.FAILED, FeatureStatus.BLOCKED):
            feature_transition(f.status, FeatureStatus.READY)
            f.status = FeatureStatus.READY
            feature_transition(f.status, FeatureStatus.RUNNING)
        f.status = FeatureStatus.RUNNING

    def _reconcile_result(self, m: Mission, f: Feature, res: WorkerResult, cfg: Config) -> None:
        f.worker.run_id = res.run_id
        f.worker.completed_at = store.utcnow()
        f.worker.exit_code = res.exit_code
        f.worker.backend = res.backend
        resolved_role = cfg.resolved_worker()
        f.worker.provider = resolved_role.get("provider") or cfg.worker.provider
        f.worker.model = resolved_role.get("model") or cfg.worker.model

        # capture the worker's transcript (redacted)
        try:
            wdir = store.workers_dir(m.repo, m.id)
            with open(os.path.join(wdir, (res.run_id or f.id) + ".txt"), "w", encoding="utf-8") as fh:
                fh.write(res.raw or "")
        except OSError:
            pass

        # git: commit in worktree, merge into base, detect conflict
        conflict = False
        commit = None
        changed = bool(res.changed_files)
        if cfg.git.enabled and GitManager(self.repo).is_repo():
            gm_wt = GitManager(f.workdir or self.repo)
            if gm_wt.is_dirty():
                gm_wt.add_all()
                commit = gm_wt.commit("feat({}): {} (hamgoose)".format(f.id, f.title))
                if commit:
                    changed = True
                    f.commits.append(commit)
            # Always attempt the merge when the feature has an isolated branch
            # (a no-op fast-forward if there is nothing new); detect conflicts.
            if (
                f.branch
                and cfg.git.auto_commit_features
                and f.workdir
                and f.workdir != self.repo
                and os.path.isdir(self._base_workdir(m))
            ):
                gm_base = GitManager(self._base_workdir(m))
                merged = gm_base.merge(f.branch)
                conflict = bool(merged.get("conflict"))
        f.result = FeatureResult(
            summary=res.summary, changed_files=res.changed_files, tests=res.tests,
            notes=res.notes, raw=(res.raw or "")[:8000],
        )

        # classify
        cls = self._classify(f, res, changed, conflict)
        if conflict:
            self._handle_conflict(m, f, cfg)
            return
        if cls is None:  # success (real changes + claimed completed)
            feature_transition(f.status, FeatureStatus.COMPLETED)
            f.status = FeatureStatus.COMPLETED
            f.failure = None
            store.append_event(m, "WORKER_FINISHED", entity=f.id, payload={"commit": commit})
            store.append_event(m, "FEATURE_COMPLETED", entity=f.id, payload={"commit": commit})
            if f.fix_of:
                self._resolve_fix(m, f)
            store.save_mission(m)
            return

        # failure path
        f.attempts += 1
        f.failure = cls.value
        f.failure_detail = redact.redact((res.blocked_reason or res.summary or res.raw or "worker failed")[:1500])
        store.append_event(m, "WORKER_FAILED", entity=f.id, payload={"class": cls.value, "attempt": f.attempts})

        if cls == FailureClass.USER_BLOCKED:
            feature_transition(f.status, FeatureStatus.BLOCKED)
            f.status = FeatureStatus.BLOCKED
            m.block_reason = "feature {} blocked (user input needed): {}".format(f.id, res.blocked_reason)
            self._transition_mission(m, MissionStatus.BLOCKED)
            store.append_event(m, "MISSION_BLOCKED", entity=m.id, payload={"reason": m.block_reason})
            store.save_mission(m)
            return

        if f.attempts >= f.max_attempts:
            feature_transition(f.status, FeatureStatus.FAILED)
            f.status = FeatureStatus.FAILED
            store.append_event(m, "FEATURE_FAILED", entity=f.id, payload={"class": cls.value})
            store.save_mission(m)
            return

        retryable = cls in RETRYABLE_FAILURES
        if retryable:
            # change strategy: keep prior failure evidence in the prompt
            feature_transition(f.status, FeatureStatus.NEEDS_FIX)
            f.status = FeatureStatus.NEEDS_FIX
            store.append_event(m, "FEATURE_RETRIED", entity=f.id, payload={"class": cls.value, "attempt": f.attempts})
        else:
            feature_transition(f.status, FeatureStatus.BLOCKED)
            f.status = FeatureStatus.BLOCKED
            m.block_reason = "feature {} hit non-retryable failure {}".format(f.id, cls.value)
            self._transition_mission(m, MissionStatus.BLOCKED)

    def _classify(self, f: Feature, res: WorkerResult, changed: bool, conflict: bool) -> Optional[FailureClass]:
        if conflict:
            return FailureClass.MERGE_CONFLICT
        if res.timed_out:
            return FailureClass.WORKER_TIMEOUT
        raw = (res.raw or "").lower()
        if res.exit_code not in (0, None):
            for sig in ("401", "403", "429", "quota", "rate limit", "api key", "provider", "unauthorized", "connection"):
                if sig in raw:
                    return FailureClass.PROVIDER_FAILURE
            return FailureClass.WORKER_CRASH
        if res.status == "blocked":
            return FailureClass.USER_BLOCKED
        if res.claimed_ok:
            if not changed and not res.changed_files:
                return FailureClass.IMPLEMENTATION_FAILURE  # claims done but no real change
            return None  # accepted
        # claimed failed
        for sig in ("test failed", "tests failed", "failed test", "assertion"):
            if sig in raw:
                return FailureClass.TEST_FAILURE
        return FailureClass.IMPLEMENTATION_FAILURE

    def _handle_conflict(self, m: Mission, f: Feature, cfg: Config) -> None:
        f.attempts += 1
        f.failure = FailureClass.MERGE_CONFLICT.value
        f.failure_detail = "merge conflict merging {} into base; work preserved, needs reconciliation".format(f.branch)
        store.append_event(m, "WORKER_FAILED", entity=f.id, payload={"class": "MERGE_CONFLICT", "attempt": f.attempts})
        # Preserve the conflicting branch/worktree (do NOT rebase: re-basing a
        # clobbering worker would silently overwrite earlier work). A real LLM
        # worker re-runs with the conflict evidence and can reconcile; identical
        # work safely blocks after bounded retries.
        if f.attempts >= f.max_attempts:
            feature_transition(f.status, FeatureStatus.BLOCKED)
            f.status = FeatureStatus.BLOCKED
            m.block_reason = "feature {} unresolved merge conflict after {} attempts; branches preserved".format(f.id, f.attempts)
            self._transition_mission(m, MissionStatus.BLOCKED)
        else:
            feature_transition(f.status, FeatureStatus.NEEDS_FIX)
            f.status = FeatureStatus.NEEDS_FIX
            store.append_event(m, "FEATURE_RETRIED", entity=f.id, payload={"class": "MERGE_CONFLICT", "note": "re-run with conflict evidence"})

    def _resolve_fix(self, m: Mission, fix: Feature) -> None:
        src = m.features.get(fix.fix_of)
        if not src:
            return
        # the source feature's defect is corrected; keep it COMPLETED and record the fix
        src.result.notes.append("corrected by {}".format(fix.id))

    # ------------------------------------------------------------------ #
    # git worktree helpers
    # ------------------------------------------------------------------ #
    def _setup_base(self, m: Mission) -> None:
        cfg = self._cfg(m)
        if not (cfg.git.enabled and GitManager(self.repo).is_repo()):
            return
        gm = GitManager(self.repo)
        if not m.base_commit:
            m.base_commit = gm.base_commit()
        base_branch = cfg.git.base_branch
        if not gm.branch_exists(base_branch):
            gm.create_branch(base_branch, m.base_commit or "HEAD")
        base_wt = self._base_workdir(m)
        if not os.path.isdir(base_wt):
            gm.add_worktree(base_wt, base_branch, create=False)

    def _base_workdir(self, m: Mission) -> str:
        return os.path.join(store.mission_dir(m.repo, m.id), "worktrees_base")

    def _validation_workdir(self, m: Mission) -> str:
        bw = self._base_workdir(m)
        return bw if os.path.isdir(bw) else self.repo

    def _worktrees_root(self, m: Mission) -> str:
        return os.path.join(store.mission_dir(m.repo, m.id), "worktrees")

    def _workdir_for(self, f: Feature, m: Mission) -> str:
        cfg = self._cfg(m)
        gm = GitManager(self.repo)
        if not (cfg.git.enabled and gm.is_repo() and cfg.git.use_worktrees):
            return self.repo
        f.branch = "{}-{}".format(cfg.git.prefix, f.id)
        path = os.path.join(self._worktrees_root(m), f.id)
        if os.path.isdir(path):
            return path
        base_branch = cfg.git.base_branch
        if gm.branch_exists(f.branch):
            gm.add_worktree(path, f.branch, create=False)
        else:
            gm.add_worktree(path, f.branch, create=True, base_ref=base_branch)
        return path

    def _reset_feature_worktree(self, f: Feature, m: Mission) -> None:
        cfg = self._cfg(m)
        gm = GitManager(self.repo)
        if not (cfg.git.enabled and gm.is_repo() and f.workdir and f.workdir != self.repo):
            return
        # Re-base the feature worktree onto the current base so the next attempt
        # re-applies its changes onto the up-to-date base (not the stale branch).
        base_branch = cfg.git.base_branch
        g = GitManager(f.workdir)
        g._run(["reset", "--hard", base_branch])
        g._run(["clean", "-fd"])

    def _base_head(self, m: Mission) -> tuple:
        gm = GitManager(self.repo)
        if not gm.is_repo():
            return m.base_commit or "", ""
        base_wt = self._base_workdir(m)
        if os.path.isdir(base_wt):
            head = GitManager(base_wt).base_commit()
        else:
            head = gm.base_commit()
        m.head_commit = head
        return m.base_commit or "", head or ""

    def _git_info(self, f: Feature, m: Mission) -> Dict[str, Any]:
        cfg = self._cfg(m)
        return {
            "enabled": cfg.git.enabled,
            "branch": f.branch,
            "workdir": f.workdir,
            "base_commit": m.base_commit,
            "base_branch": cfg.git.base_branch,
            "auto_commit": cfg.git.auto_commit_features,
        }

    # ------------------------------------------------------------------ #
    # pause / resume / steer / replan / cancel / retry / validate
    # ------------------------------------------------------------------ #
    def pause(self, mission_id_: str, reason: str = "") -> Mission:
        m = self._get(mission_id_)
        if m.status not in (MissionStatus.RUNNING, MissionStatus.VALIDATING):
            raise ValueError("cannot pause a mission in state {}".format(m.status.value))
        mission_transition(m.status, MissionStatus.PAUSED)
        m.status = MissionStatus.PAUSED
        m.pause_reason = reason
        store.append_event(m, "MISSION_PAUSED", entity=m.id, payload={"reason": reason})
        store.save_mission(m)
        return m

    def resume(self, mission_id_: str) -> Mission:
        m = self._get(mission_id_)
        if m.status not in (MissionStatus.PAUSED, MissionStatus.BLOCKED):
            raise ValueError("cannot resume a mission in state {}".format(m.status.value))
        self._reconcile(m)
        mission_transition(m.status, MissionStatus.RUNNING)
        m.status = MissionStatus.RUNNING
        m.pause_reason = ""
        m.block_reason = ""
        store.append_event(m, "MISSION_RESUMED", entity=m.id)
        store.save_mission(m)
        return m

    def steer(self, mission_id_: str, instruction: str, feature_id: Optional[str] = None, priority: Optional[int] = None) -> Mission:
        m = self._get(mission_id_)
        note = SteeringNote(kind="steer", instruction=instruction, created_at=store.utcnow(), applied=True)
        m.steering.append(note)
        payload = {"instruction": instruction}
        if feature_id and feature_id in m.features and priority is not None:
            m.features[feature_id].priority = int(priority)
            payload["feature_id"] = feature_id
            payload["priority"] = priority
        store.append_event(m, "MISSION_STEERED", entity=m.id, payload=payload)
        store.save_mission(m)
        return m

    def replan(self, mission_id_: str, instruction: str, plan_delta: Optional[Dict[str, Any]] = None) -> Mission:
        m = self._get(mission_id_)
        if m.status not in (MissionStatus.RUNNING, MissionStatus.VALIDATING, MissionStatus.PAUSED):
            raise ValueError("cannot replan a mission in state {}".format(m.status.value))
        self._transition_mission(m, MissionStatus.PAUSED)
        m.pause_reason = "replanning"
        store.append_event(m, "MISSION_REPLANNED", entity=m.id, payload={"instruction": instruction, "phase": "start"})

        if plan_delta is None:
            prompt = prompting.replan_prompt(m, instruction)
            text = self.semantic.complete(prompt, role="orchestrator")
            plan_delta = extract_json(text) or {}
        self._apply_replan(m, plan_delta, instruction)
        store.save_mission(m)
        return m

    def _apply_replan(self, m: Mission, delta: Dict[str, Any], instruction: str) -> None:
        supersede = set(delta.get("supersede", []) or [])
        remove = set(delta.get("remove", []) or [])
        # mark superseded / removed
        for fid in list(m.features.keys()):
            if fid in m.features:
                f = m.features[fid]
                if f.status not in (FeatureStatus.COMPLETED,) and fid in supersede:
                    feature_transition(f.status, FeatureStatus.SUPERSEDED)
                    f.status = FeatureStatus.SUPERSEDED
                    f.superseded_by = None
        # remove from milestone feature lists
        for ms in m.milestones.values():
            ms.features = [f for f in ms.features if f not in remove and f not in supersede]
        for fid in remove:
            m.features.pop(fid, None)
        # new milestones
        for msd in delta.get("new_milestones", []) or []:
            mid = msd.get("id") or m.next_milestone_id()
            if mid not in m.milestones:
                m.milestones[mid] = Milestone(id=mid, objective=str(msd.get("objective", "")))
        # new features
        for f in delta.get("new_features", []) or []:
            fid = m.next_feature_id()
            ft = Feature(
                id=fid, title=str(f.get("title", "")), description=str(f.get("description", "")),
                milestone=str(f.get("milestone", m.ordered_milestones()[-1].id if m.ordered_milestones() else "")),
                dependencies=list(f.get("dependencies", []) or []),
                acceptance_criteria=list(f.get("acceptance_criteria", []) or []),
                expected_paths=list(f.get("expected_paths", []) or []),
            )
            m.features[fid] = ft
            if ft.milestone in m.milestones and fid not in m.milestones[ft.milestone].features:
                m.milestones[ft.milestone].features.append(fid)
        m.current_revision += 1
        m.plan_revisions.append(PlanRevision(number=m.current_revision, created_at=store.utcnow(),
                                             note=instruction, feature_ids=list(m.features.keys()),
                                             milestone_ids=list(m.milestones.keys())))
        # ensure active milestone is a live one
        self._advance_milestone(m)
        if m.active_milestone is None and m.milestones:
            m.active_milestone = m.ordered_milestones()[0].id
        m.pause_reason = ""
        m.block_reason = ""
        self._transition_mission(m, MissionStatus.RUNNING)
        store.append_event(m, "MISSION_REPLANNED", entity=m.id, payload={"phase": "done", "note": delta.get("note", instruction)})

    def cancel(self, mission_id_: str) -> Mission:
        m = self._get(mission_id_)
        if m.status in (MissionStatus.COMPLETED, MissionStatus.FAILED, MissionStatus.CANCELLED):
            raise ValueError("mission already terminal")
        mission_transition(m.status, MissionStatus.CANCELLED)
        m.status = MissionStatus.CANCELLED
        self._cleanup_worktrees(m)
        store.append_event(m, "MISSION_CANCELLED", entity=m.id)
        store.save_mission(m)
        return m

    def retry_feature(self, mission_id_: str, feature_id: str) -> Mission:
        m = self._get(mission_id_)
        f = m.features.get(feature_id)
        if not f:
            raise ValueError("unknown feature {}".format(feature_id))
        f.failure = None
        f.failure_detail = ""
        feature_transition(f.status, FeatureStatus.READY)
        f.status = FeatureStatus.READY
        store.append_event(m, "FEATURE_RETRIED", entity=f.id, payload={"manual": True})
        store.save_mission(m)
        return m

    def validate(self, mission_id_: str, kind: str = "scrutiny") -> str:
        m = self._get(mission_id_)
        ms = self._active_milestone(m) or m.ordered_milestones()[-1]
        base, head = self._base_head(m)
        self._report("running {} validation (isolated goose run)".format(kind), 10, 100)
        r = self.validation_backend.run(kind, m, ms.id if ms else "", base, head, self._base_workdir(m), self._project_context(m))
        self._report("{} validation complete".format(kind), 100, 100)
        if ms:
            ms.validation.append(r)
        self._save_validation(m, (ms.id if ms else "final"), r)
        store.save_mission(m)
        return json.dumps({"kind": kind, "passed": r.passed, "severity": r.severity, "findings": [f.__dict__ for f in r.findings]}, indent=2)

    # ------------------------------------------------------------------ #
    # reconcile / crash recovery
    # ------------------------------------------------------------------ #
    def _reconcile(self, m: Mission) -> None:
        # A feature left RUNNING with no live process is recovered to READY so
        # work is not double-counted or lost after a crash/restart.
        for f in list(m.features.values()):
            if f.status == FeatureStatus.RUNNING:
                feature_transition(f.status, FeatureStatus.READY)
                f.status = FeatureStatus.READY
                store.append_event(m, "WORKER_RECONCILED", entity=f.id, payload={"reason": "reset stale RUNNING to READY"})
        # prune orphan feature worktrees with no commit
        cfg = self._cfg(m)
        if cfg.git.enabled and GitManager(self.repo).is_repo():
            root = self._worktrees_root(m)
            gm = GitManager(self.repo)
            for f in m.features.values():
                if f.workdir and os.path.isdir(f.workdir):
                    gm_wt = GitManager(f.workdir)
                    if not gm_wt.is_dirty() and not f.commits and f.status != FeatureStatus.COMPLETED:
                        gm.remove_worktree(f.workdir)
                        if f.branch and f.status != FeatureStatus.COMPLETED and not gm.branch_exists(f.branch):
                            gm._run(["branch", "-D", f.branch])

    def _cleanup_worktrees(self, m: Mission) -> None:
        gm = GitManager(self.repo)
        if not gm.is_repo():
            return
        root = self._worktrees_root(m)
        if os.path.isdir(root):
            for name in os.listdir(root):
                gm.remove_worktree(os.path.join(root, name))
        base_wt = self._base_workdir(m)
        if os.path.isdir(base_wt):
            gm.remove_worktree(base_wt)
        gm.prune_worktrees()

    # ------------------------------------------------------------------ #
    # misc
    # ------------------------------------------------------------------ #
    def _transition_mission(self, m: Mission, to: MissionStatus) -> None:
        if m.status == to:
            return
        mission_transition(m.status, to)
        m.status = to

    def _save_validation(self, m: Mission, milestone_id: str, r: ValidationResult) -> None:
        try:
            vdir = store.validation_dir(m.repo, m.id)
            os.makedirs(vdir, exist_ok=True)
            payload = {"kind": r.kind, "passed": r.passed, "severity": r.severity,
                       "summary": r.summary, "findings": [f.__dict__ for f in r.findings]}
            if milestone_id == "final":
                sequence = len(m.final_validation)
            else:
                ms = m.milestones.get(milestone_id)
                sequence = len(ms.validation) if ms else 1
            # Validation runs are append-only evidence. Include the current
            # count so scrutiny/user-testing reports cannot overwrite each
            # other (the old code used final_validation for every report).
            filename = "{}-{}.json".format(milestone_id, max(sequence, 1))
            with open(os.path.join(vdir, filename), "w", encoding="utf-8") as fh:
                fh.write(json.dumps(payload, indent=2))
        except Exception:
            pass

    def status(self, mission_id_: str) -> str:
        return render.mission_control(self._get(mission_id_))

    def plan_text(self, mission_id_: str) -> str:
        return render.plan_md(self._get(mission_id_))

    def readiness(self, mission_id_: str) -> str:
        m = self._get(mission_id_)
        return render.readiness_md(m.readiness)

    def list(self) -> List[Dict[str, Any]]:
        return store.list_missions(self.repo)

    def _get(self, mission_id_: str) -> Mission:
        m = store.load_mission(self.repo, mission_id_)
        if m is None:
            raise ValueError("mission not found: {}".format(mission_id_))
        return m


def _all_done(m: Mission) -> bool:
    return all(ms.status == MilestoneStatus.PASSED for ms in m.ordered_milestones()) and bool(m.ordered_milestones())
