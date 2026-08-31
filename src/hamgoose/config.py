"""Configuration model with sensible defaults and per-mission overrides.

Configuration is split into the orchestrator, worker and validator roles, plus
execution / validation / git settings. Provider and model values of "inherit"
fall back to the running Goose environment (GOOSE_PROVIDER / GOOSE_MODEL) or,
if unset, to the active provider of the host goose installation.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


def _deep_merge_cfg(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Per-key merge so higher-precedence layers only override the keys they
    actually set (a mission override of worker_timeout must not clobber the
    repo file's max_feature_attempts)."""
    out = dict(base or {})
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge_cfg(out[k], v)
        else:
            out[k] = v
    return out


class RoleConfig(BaseModel):
    provider: str = "inherit"
    model: str = "inherit"
    # Leaf Goose calls are bounded, but leave enough room for real inspection,
    # implementation, and a targeted verification pass.
    max_turns: int = Field(default=32, ge=1)



class ExecutionConfig(BaseModel):
    max_concurrent_workers: int = Field(default=2, ge=1)
    max_feature_attempts: int = Field(default=3, ge=1)
    # H3: 420 s (the old default) was below the wall time of
    # small-output-budget models (Qwen3.8-class) for multi-file features - the
    # F001/F004 timeout deaths. 900 s is the observed safe budget; for faster
    # models the cap is inert (a run ends when the model finishes, not at the
    # cap).
    worker_timeout: Optional[int] = Field(default=900, ge=1)
    # H4: 180 s killed milestone validators mid-verdict on the same model
    # class (MS01 false-block). 600 s matches the planner budget; again inert
    # on faster models. Validator infra retries double it per attempt.
    semantic_timeout: int = Field(default=600, ge=1)
    # Planner runs get their own, larger budget: decomposition reads the whole
    # repo analysis and must not share the short validator/diagnosis timeout
    # (180 s factory planner kills lost missions 3 and 4 with zero PLAN events).
    planner_timeout: int = Field(default=600, ge=1)
    # Bounded model-capability smoke test at missionCreate (HG-07). Reports
    # only; never switches models.
    model_preflight: bool = True
    # Keep each MCP call short enough for the host UI to remain responsive.
    max_steps_per_run: int = Field(default=6, ge=1)


class ValidationConfig(BaseModel):
    scrutiny: bool = True
    user_testing: bool = True
    max_correction_attempts: int = Field(default=3, ge=1)


class GitConfig(BaseModel):
    enabled: bool = True
    use_worktrees: bool = True
    auto_commit_features: bool = True
    base_branch: str = "mission/base"
    prefix: str = "mission"


class Config(BaseModel):
    orchestrator: RoleConfig = Field(default_factory=RoleConfig)
    worker: RoleConfig = Field(default_factory=RoleConfig)
    validator: RoleConfig = Field(default_factory=RoleConfig)
    # H2: the planner is an LLM call like any other and MUST be pinnable.
    # The old schema silently dropped this key, so planner calls inherited the
    # default provider and died on its rate limit (missions M-2026-013808F8 /
    # M-2026-0142179C). Resolution order per field: planner -> orchestrator ->
    # GOOSE_PROVIDER / GOOSE_MODEL.
    planner: RoleConfig = Field(default_factory=RoleConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    validation: ValidationConfig = Field(default_factory=ValidationConfig)
    git: GitConfig = Field(default_factory=GitConfig)
    #: H2: top-level keys that were silently ignored by this load. Surfaced by
    # mission_create instead of being dropped without a trace.
    unrecognized_keys: List[str] = Field(default_factory=list, exclude=True)

    def resolved_worker(self) -> Dict[str, Any]:
        return self._resolve(self.worker)

    def resolved_orchestrator(self) -> Dict[str, Any]:
        """Resolve the orchestrator role's inherited provider/model values."""
        return self._resolve(self.orchestrator)

    def resolved_validator(self) -> Dict[str, Any]:
        return self._resolve(self.validator)

    def resolved_planner(self) -> Dict[str, Any]:
        """Planner resolution with per-field fallback to the orchestrator
        role, then to the environment (H2)."""
        p = self._resolve(self.planner)
        o = self._resolve(self.orchestrator)
        return {
            "provider": p["provider"] or o["provider"],
            "model": p["model"] or o["model"],
            "max_turns": self.planner.max_turns,
        }

    def _resolve(self, role: RoleConfig) -> Dict[str, Any]:
        provider = role.provider if role.provider != "inherit" else os.environ.get("GOOSE_PROVIDER", "")
        model = role.model if role.model != "inherit" else os.environ.get("GOOSE_MODEL", "")
        return {"provider": provider, "model": model, "max_turns": role.max_turns}

    @classmethod
    def load(cls, overrides: Optional[Dict[str, Any]] = None, repo: Optional[str] = None) -> "Config":
        """Resolve config with explicit precedence (HG-08):

        1. HAMGOOSE_CONFIG env (JSON)
        2. <repo>/.goose/hamgoose/config.json  (per-repo defaults)
        3. per-mission overrides (highest)

        Hand-editing a single mission's mission.json therefore no longer
        bypasses the other missions: repo-level defaults live in ONE file and
        every mission created in that repo picks them up.
        """
        data: Dict[str, Any] = {}
        env_cfg = os.environ.get("HAMGOOSE_CONFIG")
        if env_cfg:
            try:
                data.update(json.loads(env_cfg))
            except json.JSONDecodeError:
                pass
        if repo:
            path = os.path.join(os.path.abspath(repo), ".goose", "hamgoose", "config.json")
            try:
                with open(path, encoding="utf-8") as f:
                    file_cfg = json.load(f)
                if isinstance(file_cfg, dict):
                    data = _deep_merge_cfg(data, file_cfg)
            except (OSError, json.JSONDecodeError):
                pass  # absent or malformed: env/defaults still apply
        if overrides:
            data = _deep_merge_cfg(data, overrides)
        if "max_concurrent_workers" in data:
            data.setdefault("execution", {})["max_concurrent_workers"] = data.pop("max_concurrent_workers")
        cfg = cls(**data)
        # H2: pydantic silently drops unknown keys - record them so mission
        # setup can refuse/warn instead of losing a pin like config.planner
        # was lost in hamgoose 0.1.8.
        cfg.unrecognized_keys = sorted(set(data) - set(cls.model_fields) - {"max_concurrent_workers"})
        return cfg

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()
