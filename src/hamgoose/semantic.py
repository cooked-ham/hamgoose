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
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from . import redact
from .config import Config

_FENCE = "```"


@dataclass
class SemanticResult:
    """Outcome of one isolated semantic call, with the evidence needed to
    classify deaths (HG-06): a bare string cannot distinguish 'the model
    answered' from 'the leaf was killed at the timeout and we kept the tail'."""
    text: str = ""
    timed_out: bool = False
    exit_code: Optional[int] = None
    duration: float = 0.0
    raw_tail: str = ""  # redacted tail of the raw process output

    @property
    def ok(self) -> bool:
        return bool(self.text) and not self.timed_out


def extract_text(stdout: str, stderr: str = "") -> str:
    """Extract the final assistant text from a `goose run --output-format json`
    payload (a list of messages), falling back to raw output."""
    out = (stdout or "").strip()
    try:
        data = json.loads(out)
    except (json.JSONDecodeError, ValueError):
        # Goose may emit a startup banner before the JSON payload unless
        # --quiet is used. Decode the first complete JSON value after it.
        # A non-JSON preface (banner, warnings) is discarded, never merged
        # into the message text.
        data = None
        decoder = json.JSONDecoder()
        starts = sorted(
            (start, marker)
            for marker in ("{", "[")
            if (start := out.find(marker)) >= 0
        )
        for start, _marker in starts:
            if start < 0:
                continue
            try:
                data, _ = decoder.raw_decode(out[start:])
                break
            except json.JSONDecodeError:
                continue
    if isinstance(data, (dict, list)):
        msgs = data if isinstance(data, list) else data.get("messages")
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
    if isinstance(data, dict):
        if isinstance(data.get("text"), str):
            return redact.redact(data["text"])
        if isinstance(data.get("response"), str):
            return redact.redact(data["response"])
    # Not JSON at all: prefer stderr trailing warnings over the banner noise,
    # then the raw stdout. If stdout is pure noise (banner/control chars) and
    # stderr looks like content, use that; otherwise keep the stdout tail.
    stderr = (stderr or "").strip()
    if not data:
        if stderr and ("error" in stderr.lower() or len(stderr) > len(out)):
            return redact.redact(stderr[-4000:])
        tail = out[-4000:] if len(out) > 4000 else out
        return redact.redact(tail)
    return redact.redact(out)


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
        """Compatibility wrapper: returns only the text (validator, replan)."""
        return self.complete_detailed(prompt, role=role).text

    def complete_detailed(
        self,
        prompt: str,
        role: str = "orchestrator",
        timeout: Optional[int] = None,
        max_turns: Optional[int] = None,
    ) -> SemanticResult:
        """Run one semantic call and return the full outcome (HG-06).

        `timeout` overrides the role's default (semantic_timeout) — the planner
        passes planner_timeout; `max_turns` overrides the role's turn budget
        (preflight smoke uses 2).
        """
        if self.backend == "sampling" and self.sampler is not None:
            try:
                res = self.sampler(prompt)
                if asyncio.iscoroutine(res):
                    try:
                        res = asyncio.get_event_loop().run_until_complete(res)
                    except RuntimeError:
                        res = asyncio.run(res)
                return SemanticResult(text=str(res))
            except RuntimeError:
                return SemanticResult(text=str(asyncio.run(self.sampler(prompt))))
            except Exception as e:  # sampler failure must not be silent
                return SemanticResult(text="", raw_tail=redact.redact(str(e))[:2000])
        return self._goose_run(prompt, role, timeout=timeout, max_turns=max_turns)

    def smoke(self, prompt: str, role: str = "worker", timeout: int = 60, max_turns: int = 2) -> SemanticResult:
        """Bounded model-capability probe (HG-07): a tiny fenced-JSON task on
        the resolved worker model. Reports only; never switches models."""
        return self.complete_detailed(prompt, role=role, timeout=timeout, max_turns=max_turns)

    def _goose_run(self, prompt: str, role: str, timeout: Optional[int] = None, max_turns: Optional[int] = None) -> SemanticResult:
        import time

        role_cfg = self._role(role)
        if timeout is None:
            timeout = self.config.execution.semantic_timeout
        if max_turns is None:
            max_turns = role_cfg["max_turns"]
        fd, path = tempfile.mkstemp(suffix=".md")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(prompt)
            cmd = ["goose", "run", "-i", path, "--output-format", "json", "--quiet", "--no-session",
                   "--max-turns", str(max_turns)]
            # "inherit" is a hamgoose sentinel, not a Goose provider name.
            # Omit inherited values so the child Goose process uses its active
            # provider/model instead of failing with Unknown provider: inherit.
            if role_cfg.get("provider") and role_cfg.get("provider") != "inherit":
                cmd += ["--provider", role_cfg["provider"]]
            if role_cfg.get("model") and role_cfg.get("model") != "inherit":
                cmd += ["--model", role_cfg["model"]]
            from . import gosub

            started = time.monotonic()
            try:
                stdout, stderr, exit_code, timed_out = gosub.run_captured(cmd, timeout=timeout)
            except OSError as e:
                return SemanticResult(text="", exit_code=None, duration=time.monotonic() - started,
                                      raw_tail=redact.redact(str(e))[:2000])
            duration = time.monotonic() - started
            text = extract_text(stdout, stderr)
            raw_tail = redact.redact(((stdout or "") + "\n--stderr--\n" + (stderr or ""))[-2000:])
            return SemanticResult(text=text, timed_out=timed_out, exit_code=exit_code,
                                  duration=duration, raw_tail=raw_tail)
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
        if role == "planner":
            # H2: planner pins resolve through config.planner (falling back to
            # orchestrator, then the environment) instead of always riding the
            # orchestrator role.
            return self.config.resolved_planner()
        return self.config.resolved_orchestrator()
