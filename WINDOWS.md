# hamgoose — Windows gotchas & hardening

Field notes from five real missions on Windows. These cost real wall-clock and
forensics time; they are documented here so the pipeline (and future operators)
can recognize and avoid them. Each is either fixed in code or worked around in
tests/CI.

## 1. `cmd` truncates multi-line command strings

The shell here is `cmd.exe`. A command string containing a newline is **silently
truncated at the first newline**. Consequences:

- `python -c "...\n..."` with embedded newlines runs only up to the first line.
- Any inline multi-line script must be one physical line, or written to a file.

Workaround: keep one-liners single-line, or write a script file and run it.

## 2. cp1252 stdout mangles UTF-8

The Windows console defaults to the `cp1252` code page. Python printing UTF-8
(non-ASCII) to stdout can raise or produce mojibake. For any tool/script that
prints rich text under `cmd`, reconfigure early:

```python
import sys
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
```

## 3. CRLF sources: normalize → edit → restore

Python files edited across tools can flip between CRLF and LF, producing noisy
diffs. The disciplined edit is: read with normalized newlines, edit, and write
back with the file's original line-ending convention so only real changes show.

## 4. `taskkill /F /T` for whole-process-tree kills

A `goose run` leaf is only the direct child; it spawns extension servers and
helpers. Killing just the direct child orphans grandchildren. The whole tree must
die:

```
taskkill /F /T /PID <pid>
```

`gosub._kill_tree` uses exactly this on Windows (`CREATE_NEW_PROCESS_GROUP` on
spawn, so the group can also be signaled).

## 5. Grandchild pipe hang (the big one)

`goose run` grandchildren inherit the parent's stdout/stderr pipes. On Windows,
reading those with pipes + `communicate()` can **hang forever** after the direct
child exits, because a grandchild keeps the write-end open. The fix (in
`gosub.run_captured`) is to redirect the child's output to **temp files** and use
`proc.wait()` (which tracks only the direct child's handle), then read the temp
files and delete them. This is the single choke point for every leaf spawn
(worker, planner, validator), so all of them are immune.

## 6. pytest temp-dir permission (fixed in-repo)

The default pytest basetemp (`%TEMP%\pytest-of-<user>`) can be permission-broken,
which errors the entire session (35 `PermissionError: [WinError 5]` failures were
observed). This is now handled **in `pyproject.toml`**:

```toml
[tool.pytest.ini_options]
addopts = ["--basetemp=.pytest_tmp"]   # repo-local, git-ignored
```

So `pytest` runs with zero manual flags, on CI or locally. (A stale locked
`.pytest_cache\v\cache` file can also cause a harmless `WinError 183` *warning*;
delete `.pytest_cache` if you see it — it does not affect pass/fail.)

## 7. Test scratch repos live inside the project worktree

`tmp_path` scratch git repos are created under this project's own worktree, so a
scratch dir "not in a repo" is impossible with `tmp_path` alone. Tests that need a
genuinely non-repo directory use `tempfile.mkdtemp()` under `%TEMP%` (see
`test_repo_analysis.py::test_is_repo_not_a_repo`).
