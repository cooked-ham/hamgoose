"""Semantic client: routes LLM-based reasoning tasks to an isolated Goose run.

hamgoose delegates semantic work (feature decomposition, failure diagnosis,
replanning, validation interpretation) to an isolated Goose context. Two backends
are supported:

- GooseRunSemantic (default): spawns 'goose run' as a leaf text-only process.
  Reliable, isolated, works headless AND in Desktop, needs no API key beyond the
  one Goose already uses. This is the documented, chosen default.
- SamplingSemantic: uses MCP sampling (host-LLM completion) when the host
  advertises it. Optional; off by default because it couples the extension to a
  live host session and complicates headless execution.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
from typing import Any, Callable, Dict, Optional

from . import redact
from .config import Config

_FENCE = "```"


def extract_text(stdout: str, stderr: str = "") -> str:
    """Extract the final assistant text from a `goose run --output-format json`
    payload (a list of messages), falling back to raw output."""
    out = (stdout or "").strip()
    try:
        data = json.loads(out)
    except (json.JSONDecodeError, ValueError):
        data = None
    if isinstance(data, dict):
        msgs = data.get("messages")
        if isinstance(msgs, list):
            for msg in reversed(msgs):
                if isinstance(msg, dict) and msg.get("role") == "assistant":
                    parts = [
                        c.get("text")
                        for c in (msg.get("content") or [])
                        if isinstance(c, dict) and c.get("type") == "text" and c.get("text")
                    ]
                    if parts:
                        return redact.redact("\n".join(parts))
        if isinstance(data.get("text"), str):
            return redact.redact(data["text"])
        if isinstance(data.get("response"), str):
            return redact.redact(data["response"])
    return redact.redact(out + (("\n" + stderr) if stderr else ""))


def extract_json(text: str) -> Optional[Dict[str, Any]]:
    """Pull the last fenced JSON object out of a model response."""
    if not text:
        return None
    candidates = []
    for m in re.finditer(_FENCE + r"(?:json)?\s*(\{.*?\})\s*" + _FENCE, text, re.S):
        candidates.append(m.group(1))
    if not candidates:
        for m in re.finditer(r"(\{.*\})", text, re.S):
            candidates.append(m.group(1))
    for cand in reversed(candidates):
        try:
            return json.loads(cand)
        except json.JSONDecodeError:
            continue
    return None


class SemanticClient:
    def __init__(self, config: Config, backend: str = "goose_run", sampler: Optional[Callable[[str], str]] = None):
        self.config = config
        self.backend = backend
        self.sampler = sampler  # optional sync-or-async callable(prompt)->str

    def complete(self, prompt: str, role: str = "orchestrator") -> str:
        if self.backend == "sampling" and self.sampler is not None:
            try:
                res = self.sampler(prompt)
                if asyncio.iscoroutine(res):
                    try:
                        res = asyncio.get_event_loop().run_until_complete(res)
                    except RuntimeError:
                        res = asyncio.run(res)
                return str(res)
            except RuntimeError:
                res = asyncio.run(self.sampler(prompt))
                return str(res)
        return self._goose_run(prompt, role)

    def _goose_run(self, prompt: str, role: str) -> str:
        role_cfg = self._role(role)
        fd, path = tempfile.mkstemp(suffix=".md")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(prompt)
            cmd = ["goose", "run", "-i", path, "--output-format", "json", "--no-session",
                   "--max-turns", str(role_cfg["max_turns"])]
            if role_cfg.get("provider"):
                cmd += ["--provider", role_cfg["provider"]]
            if role_cfg.get("model"):
                cmd += ["--model", role_cfg["model"]]
            from . import gosub

            try:
                stdout, stderr, _exit, _to = gosub.run_captured(cmd, timeout=900)
            except OSError as e:
                return redact.redact(str(e))
            return extract_text(stdout, stderr)
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    def _parse_run(self, stdout: str, stderr: str) -> str:
        return extract_text(stdout, stderr)

    def _role(self, role: str) -> Dict[str, str]:
        if role == "validator":
            return self.config.resolved_validator()
        if role == "worker":
            return self.config.resolved_worker()
        return {
            "provider": self.config.orchestrator.provider,
            "model": self.config.orchestrator.model,
            "max_turns": self.config.orchestrator.max_turns,
        }
