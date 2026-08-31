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


class RoleConfig(BaseModel):
    provider: str = "inherit"
    model: str = "inherit"
    max_turns: int = Field(default=100, ge=1)


class ExecutionConfig(BaseModel):
    max_concurrent_workers: int = Field(default=2, ge=1)
    max_feature_attempts: int = Field(default=3, ge=1)
    worker_timeout: Optional[int] = Field(default=None, ge=1)
    max_steps_per_run: int = Field(default=50, ge=1)


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
    def load(cls, overrides: Optional[Dict[str, Any]] = None) -> "Config":
        data: Dict[str, Any] = {}
        env_cfg = os.environ.get("HAMGOOSE_CONFIG")
        if env_cfg:
            try:
                data.update(json.loads(env_cfg))
            except json.JSONDecodeError:
                pass
        if overrides:
            data.update(overrides)
        if "max_concurrent_workers" in data:
            data.setdefault("execution", {})["max_concurrent_workers"] = data.pop("max_concurrent_workers")
        return cls(**data)

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()
