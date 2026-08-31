"""Robust subprocess helper for launching `goose run`.

`goose run` spawns child processes (extension servers, helpers) that inherit the
parent's stdout/stderr. On Windows, reading those with pipes + communicate() can
hang forever after the direct child exits because a grandchild keeps the pipe
open. Capturing output to temp files and using proc.wait() (which only tracks the
direct child's handle) avoids the hang. Timeouts kill the whole process tree.

Environment sanitization: the npm launcher marks its server child with
`HAMGOOSE_LAUNCHER=1` (its recursion guard). That marker would leak into every
leaf `goose run` this module spawns, so the leaf Goose loads the registered
`hamgoose` extension, the launcher trips its own guard, and the leaf stderr
gets a spurious "recursion guard tripped — refusing to spawn" warning. Leaves
never need the marker (they can resolve the real server normally), so we strip
it here — the single choke point for all leaf spawns (workers, planners,
validators).
"""
from __future__ import annotations

import os
import signal
import subprocess
import tempfile
from typing import Callable, Dict, List, Optional, Tuple


def _kill_tree(proc: subprocess.Popen) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                       capture_output=True, errors="replace")
    else:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, OSError):
            try:
                proc.kill()
            except OSError:
                pass


def run_captured(
    cmd: List[str],
    cwd: Optional[str] = None,
    timeout: Optional[int] = None,
    env: Optional[Dict[str, str]] = None,
    on_poll: Optional[Callable[[str, str, float], None]] = None,
    poll_interval: float = 5.0,
) -> Tuple[str, str, Optional[int], bool]:
    """Run cmd, capturing stdout/stderr via temp files.

    Returns (stdout, stderr, exit_code, timed_out).

    `on_poll(out_path, err_path, elapsed)` — optional watcher invoked every
    `poll_interval` seconds while the process is alive (HG-14): lets callers
    observe a long leaf run (bytes written, turn hints) instead of 420 s of
    silence. Polling stops when the process exits or the timeout fires.
    """
    import time
    fd_out, out_path = tempfile.mkstemp(suffix=".out")
    os.close(fd_out)
    fd_err, err_path = tempfile.mkstemp(suffix=".err")
    os.close(fd_err)

    child_env = dict(env if env is not None else os.environ)
    child_env.pop("HAMGOOSE_LAUNCHER", None)  # see module docstring
    kwargs = {"cwd": cwd, "env": child_env}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

    timed_out = False
    exit_code: Optional[int] = None
    try:
        with open(out_path, "w", encoding="utf-8", errors="replace") as of, \
                open(err_path, "w", encoding="utf-8", errors="replace") as ef:
            proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=of, stderr=ef, **kwargs)
            started = time.monotonic()
            while True:
                remaining = None if timeout is None else timeout - (time.monotonic() - started)
                if remaining is not None and remaining <= 0:
                    timed_out = True
                    _kill_tree(proc)
                    try:
                        proc.wait(timeout=20)
                    except subprocess.TimeoutExpired:
                        pass
                    break
                wait_for = poll_interval if remaining is None else max(0.05, min(poll_interval, remaining))
                try:
                    proc.wait(timeout=wait_for)
                    break  # process exited
                except subprocess.TimeoutExpired:
                    if on_poll is not None:
                        try:
                            on_poll(out_path, err_path, time.monotonic() - started)
                        except Exception:
                            pass  # progress observation must never kill the run
            exit_code = proc.returncode
    finally:
        pass

    def _read(p: str) -> str:
        try:
            with open(p, encoding="utf-8", errors="replace") as fh:
                return fh.read()
        except OSError:
            return ""

    stdout, stderr = _read(out_path), _read(err_path)
    for p in (out_path, err_path):
        try:
            os.remove(p)
        except OSError:
            pass
    return stdout, stderr, exit_code, timed_out
