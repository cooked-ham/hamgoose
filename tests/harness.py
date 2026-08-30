"""Shared test harness: deterministic mock-backed controllers + git helpers."""
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from hamgoose.config import Config
from hamgoose.controller import MissionController
from hamgoose.validator import MockValidationBackend
from hamgoose.worker import MockBackend


def make_controller(repo, simulator=None, checker=None, config_over=None, git=False):
    cfg = Config.load(config_over)
    if not git:
        cfg.git.enabled = False
        cfg.git.use_worktrees = False
    ctl = MissionController(repo, cfg)
    ctl.worker_backend = MockBackend(simulator)
    ctl.validation_backend = MockValidationBackend(checker)
    return ctl


def create_and_plan(ctl, goal, features, milestones):
    m = ctl.create_mission(goal)
    ctl.plan(m.id, features=features, milestones=milestones)
    return ctl._get(m.id)


def F(fid, title, deps=None, ms="MS01", paths=None, criteria=None, **kw):
    d = {
        "id": fid,
        "title": title,
        "description": "desc for " + fid,
        "milestone": ms,
        "dependencies": deps or [],
        "acceptance_criteria": criteria or ["done"],
        "expected_paths": paths or [],
    }
    d.update(kw)
    return d


def MS(mid, obj, crit=None):
    return {"id": mid, "objective": obj, "completion_criteria": crit or ["ok"]}


def init_git(repo):
    env = dict(os.environ)
    env.update(GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")

    def g(*args):
        subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, env=env, check=True)

    g("init", "-q")
    with open(os.path.join(repo, "shared.txt"), "w") as f:
        f.write("base\n")
    g("add", "-A")
    g("commit", "-q", "-m", "init")
    return env
