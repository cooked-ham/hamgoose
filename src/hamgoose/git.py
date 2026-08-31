"""Git / worktree integration.

Git is treated as the authoritative record of implementation changes. All
operations go through the system git binary via subprocess. Every method is
defensive: if git is unavailable or a command fails, the method returns a
sentinel (None / False / empty) rather than raising, so the orchestrator can
degrade gracefully (e.g. run without Git).
"""
from __future__ import annotations

import os
import subprocess
from typing import Dict, List, Optional


class GitManager:
    def __init__(self, repo: str):
        self.repo = os.path.abspath(repo)

    def _run(self, args: List[str], cwd: Optional[str] = None, timeout: int = 120) -> Optional[str]:
        try:
            proc = subprocess.run(
                ["git", *args],
                cwd=cwd or self.repo,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
            if proc.returncode != 0:
                return None
            return proc.stdout.strip()
        except (subprocess.SubprocessError, OSError):
            return None

    # -- queries ----------------------------------------------------------- #
    def is_repo(self) -> bool:
        return self._run(["rev-parse", "--is-inside-work-tree"]) == "true"

    def base_commit(self) -> Optional[str]:
        return self._run(["rev-parse", "HEAD"])

    def current_branch(self) -> Optional[str]:
        return self._run(["rev-parse", "--abbrev-ref", "HEAD"])

    def is_dirty(self, cwd: Optional[str] = None) -> bool:
        out = self._run(["status", "--porcelain"], cwd=cwd)
        return bool(out)

    def status(self) -> Dict[str, object]:
        return {
            "is_repo": self.is_repo(),
            "branch": self.current_branch(),
            "base_commit": self.base_commit(),
            "dirty": self.is_dirty(),
        }

    def changed_files(self, since: Optional[str] = None) -> List[str]:
        out = self._run(["diff", "--name-only", "--", since or "HEAD"] if since else ["status", "--porcelain"])
        if not out:
            return []
        return [line for line in out.splitlines() if line.strip()]

    # -- branches / worktrees -------------------------------------------- #
    def branch_exists(self, name: str) -> bool:
        return self._run(["rev-parse", "--verify", name]) is not None

    def create_branch(self, name: str, from_ref: str = "HEAD") -> bool:
        return self._run(["branch", name, from_ref]) is not None

    def create_worktree(self, path: str, branch: str) -> Optional[str]:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        res = self._run(["worktree", "add", "-b", branch, path, "HEAD"])
        return path if res is not None or self._run(["rev-parse", "--verify", branch]) else None

    def remove_worktree(self, path: str) -> bool:
        self._run(["worktree", "remove", "--force", path])
        return not os.path.isdir(path)

    def add_worktree(self, path: str, branch: str, create: bool = False, base_ref: str = "HEAD") -> Optional[str]:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        if create:
            self._run(["branch", branch, base_ref])
            self._run(["worktree", "add", path, branch])
        else:
            self._run(["worktree", "add", path, branch])
        ok = self._run(["worktree", "list"]) is not None and os.path.isdir(path)
        return path if ok else None

    def checkout(self, branch: str, cwd: Optional[str] = None) -> bool:
        return self._run(["checkout", branch], cwd=cwd) is not None

    def prune_worktrees(self) -> None:
        self._run(["worktree", "prune"])

    # -- commits ---------------------------------------------------------- #
    def add_all(self, cwd: Optional[str] = None) -> bool:
        return self._run(["add", "-A"], cwd=cwd) is not None

    def commit(self, message: str, cwd: Optional[str] = None) -> Optional[str]:
        env = dict(os.environ)
        env.setdefault("GIT_AUTHOR_NAME", "hamgoose")
        env.setdefault("GIT_AUTHOR_EMAIL", "hamgoose@localhost")
        env.setdefault("GIT_COMMITTER_NAME", "hamgoose")
        env.setdefault("GIT_COMMITTER_EMAIL", "hamgoose@localhost")
        try:
            proc = subprocess.run(
                ["git", "commit", "-m", message, "--allow-empty"],
                cwd=cwd or self.repo,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=120,
                env=env,
            )
            if proc.returncode != 0:
                return None
            return self._run(["rev-parse", "HEAD"], cwd=cwd)
        except (subprocess.SubprocessError, OSError):
            return None

    def diff(self, a: str, b: str) -> str:
        return self._run(["diff", "--", a, b]) or ""

    # -- merge ------------------------------------------------------------ #
    def merge(self, branch: str, cwd: Optional[str] = None) -> Dict[str, object]:
        """Merge branch into cwd (base). Returns {ok, conflict, message}."""
        res = self._run(["merge", "--no-ff", "-m", "merge", branch], cwd=cwd)
        if res is None:
            # merge may have failed (conflict). detect.
            conflicts = self._run(["diff", "--name-only", "--diff-filter=U"], cwd=cwd)
            if conflicts:
                self._run(["merge", "--abort"], cwd=cwd)
                return {"ok": False, "conflict": True, "message": conflicts}
            return {"ok": False, "conflict": False, "message": res or "merge failed"}
        return {"ok": True, "conflict": False, "message": res}
