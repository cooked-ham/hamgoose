"""One-shot registration/removal of hamgoose in Goose's config.yaml.

Optional convenience only: Goose's own UI does the same thing
interactively (`goose configure` -> Extensions -> Add Extension). This
module exists so the install path is a single command even headless:

    pip install .        # makes the `hamgoose` console script available
    hamgoose register    # merges one stdio entry into Goose's config.yaml
    hamgoose unregister  # removes it again

The write is atomic and always leaves a `.bak` of the previous file.

Schema note: Goose >= 1.48 expects stdio entries as `cmd` (single
executable) + `args` (list). Entries written with the older `command:`
field are rejected by Goose ("Skipping malformed extension config entry");
`register` detects and repairs them in place (status "repaired").
"""
from __future__ import annotations

import argparse
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

import yaml

DEFAULT_NAME = "hamgoose"
DESCRIPTION = "Mission orchestration for Goose"

_USAGE = """\
hamgoose - Mission orchestration extension for Goose

usage:
  hamgoose               run the MCP stdio server (what Goose launches)
  hamgoose register      add hamgoose to Goose's config.yaml (one command)
  hamgoose unregister    remove hamgoose from Goose's config.yaml
  hamgoose help          show this message

The same registration can be done interactively in Goose itself:
  goose configure  ->  Extensions  ->  Add Extension  (Type: STDIO)
"""


def find_goose_config_file() -> Path:
    """Locate Goose's config.yaml.

    Order: HAMGOOSE_CONFIG_FILE override (tests/CI), `goose info`
    output, then the platform default location.
    """
    override = os.environ.get("HAMGOOSE_CONFIG_FILE")
    if override:
        return Path(override)
    if shutil.which("goose"):
        try:
            proc = subprocess.run(
                ["goose", "info"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=30,
            )
            out = proc.stdout.decode("utf-8", errors="replace")
            match = re.search(r"Config yaml:\s*(\S+)", out)
            if match:
                return Path(match.group(1))
        except Exception:
            pass
    if platform.system() == "Windows":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "Block" / "goose" / "config" / "config.yaml"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    root = Path(xdg) if xdg else Path.home() / ".config"
    return root / "goose" / "config" / "config.yaml"


def resolve_command() -> str:
    """Command line Goose should use to spawn hamgoose.

    Prefers the bare `hamgoose` console script when it resolves on PATH
    (portable, no venv paths in config). Falls back to this
    interpreter's `python -m hamgoose` so the entry still works when the
    venv is not on PATH.
    """
    if shutil.which("hamgoose"):
        return "hamgoose"
    exe = str(Path(sys.executable).resolve())
    return f"{exe} -m hamgoose"


def _load(config_file: Path) -> dict:
    if not config_file.exists():
        return {}
    try:
        data = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise SystemExit(f"error: cannot parse {config_file} as YAML ({exc}); fix it manually first")
    return data if isinstance(data, dict) else {}


def _save(config_file: Path, data: dict) -> None:
    config_file.parent.mkdir(parents=True, exist_ok=True)
    if config_file.exists():
        shutil.copy2(config_file, str(config_file) + ".bak")
    tmp = config_file.with_name(config_file.name + ".tmp")
    tmp.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False),
        encoding="utf-8",
    )
    os.replace(tmp, config_file)


def _stdio_entry(name: str) -> dict:
    """Build the stdio entry in the current Goose schema (cmd + args list).

    Goose >= 1.48 expects `cmd` (single executable) plus `args` (list); the
    older `command: "<full line>"` shape is rejected with
    "Skipping malformed extension config entry" and never loads.
    """
    parts = resolve_command().split()
    return {
        "enabled": True,
        "type": "stdio",
        "name": name,
        "description": DESCRIPTION,
        "cmd": parts[0],
        "args": parts[1:],
    }


def register(config_file: Optional[Path] = None, name: str = DEFAULT_NAME, force: bool = False) -> dict:
    """Add/refresh the hamgoose stdio extension entry. Returns a result dict."""
    path = Path(config_file) if config_file else find_goose_config_file()
    data = _load(path)
    exts = data.get("extensions")
    if not isinstance(exts, dict):
        exts = {}
    existing = exts.get(name)
    if isinstance(existing, dict) and "cmd" not in existing:
        # Legacy/broken entry (old `command:` schema or missing fields).
        # Goose >= 1.48 skips malformed entries, so repair it in place —
        # otherwise `register` would forever report "already_registered".
        entry = _stdio_entry(name)
        exts[name] = entry
        data["extensions"] = exts
        _save(path, data)
        return {"status": "repaired", "config": str(path), "entry": entry}
    if existing is not None and not force:
        return {"status": "already_registered", "config": str(path), "entry": existing}
    entry = _stdio_entry(name)
    exts[name] = entry
    data["extensions"] = exts
    _save(path, data)
    return {"status": "registered", "config": str(path), "entry": entry}

def unregister(config_file: Optional[Path] = None, name: str = DEFAULT_NAME) -> dict:
    """Remove the hamgoose entry. Returns a result dict."""
    path = Path(config_file) if config_file else find_goose_config_file()
    data = _load(path)
    exts = data.get("extensions")
    if not isinstance(exts, dict) or name not in exts:
        return {"status": "not_registered", "config": str(path)}
    removed = exts.pop(name)
    if not exts:
        data.pop("extensions", None)
    _save(path, data)
    return {"status": "unregistered", "config": str(path), "entry": removed}


def cli_main(argv: Optional[list] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("help", "--help", "-h"):
        print(_USAGE)
        return 0
    cmd, rest = argv[0], argv[1:]
    parser = argparse.ArgumentParser(prog=f"hamgoose {cmd}")
    parser.add_argument("--name", default=DEFAULT_NAME, help="extension name (default: hamgoose)")
    parser.add_argument("--config", default=None, help="config.yaml path (default: auto-detect via `goose info`)")
    parser.add_argument("--force", action="store_true", help="overwrite an existing entry")
    args = parser.parse_args(rest)
    path = Path(args.config) if args.config else None
    if cmd in ("register", "add"):
        result = register(path, args.name, args.force)
    elif cmd in ("unregister", "remove"):
        result = unregister(path, args.name)
    else:
        print(_USAGE)
        return 2
    print(f"[{result['status']}] {args.name} @ {result['config']}")
    entry = result.get("entry")
    if isinstance(entry, dict):
        for key, value in entry.items():
            print(f"    {key}: {value}")
    if result["status"] == "registered":
        print("Done. Start a new Goose session; the mission_* tools will appear.")
        print("Manage it any time: goose configure -> Extensions (toggle / remove), or `hamgoose unregister`.")
    return 0
