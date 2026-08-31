"""Configuration model with sensible defaults and per-mission overrides.

Configuration is split into the orchestrator, worker and validator roles, plus
execution / validation / git settings. Provider and model values of "inherit"
fall back to the running Goose environment (GOOSE_PROVIDER / GOOSE_MODEL) or,
if unset, to the active provider of the host goose installation.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

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
    worker_timeout: Optional[int] = Field(default=420, ge=1)
    semantic_timeout: int = Field(default=180, ge=1)
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
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    validation: ValidationConfig = Field(default_factory=ValidationConfig)
    git: GitConfig = Field(default_factory=GitConfig)

    def resolved_worker(self) -> Dict[str, Any]:
        return self._resolve(self.worker)

    def resolved_orchestrator(self) -> Dict[str, Any]:
        """Resolve the orchestrator role's inherited provider/model values."""
        return self._resolve(self.orchestrator)

    def resolved_validator(self) -> Dict[str, Any]:
        return self._resolve(self.validator)

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
        return cls(**data)

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()
