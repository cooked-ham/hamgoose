"""HG-13: first-class external-implementation path."""
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from harness import F, MS, init_git, make_controller  # noqa: E402

from hamgoose import store  # noqa: E402
from hamgoose.models import FeatureStatus, MilestoneStatus  # noqa: E402


def _real_commit(git_repo, filename, content):
    with open(os.path.join(git_repo, filename), "w") as f:
        f.write(content)
    subprocess.run(["git", "add", "-A"], cwd=git_repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-q", "-m", "external work"], cwd=git_repo,
                   check=True, capture_output=True,
                   env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                            GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"))
    out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=git_repo, check=True,
                         capture_output=True, text=True)
    return out.stdout.strip()


def test_external_completion_full_path(git_repo):
    ctl = make_controller(git_repo, git=True)
    m = ctl.create_mission("goal")
    ctl.plan(m.id, features=[dict(F("F001", "the thing"),
                                  validation_commands=["echo ok"])],
             milestones=[MS("MS01", "o")])
    ctl.approve(m.id)

    # mock worker fails the feature outright
    ctl.worker_backend.simulator = lambda f, wd: {"status": "failed", "summary": "cannot",
                                                  "changed_files": [], "notes": []}
    ctl.run(m.id)
    m = ctl._get(m.id)
    assert m.features["F001"].status == FeatureStatus.FAILED

    # lead agent implements it for real
    commit = _real_commit(git_repo, "impl.py", "VALUE = 42\n")
    m = ctl.complete_feature_external(m.id, "F001", "implemented by the lead agent",
                                      commit=commit, tests=["ran pytest: green"])

    f = m.features["F001"]
    assert f.status == FeatureStatus.COMPLETED
    assert commit in f.commits
    assert f.worker.backend == "external"
    assert f.result.changed_files  # derived from the commit
    assert "completed externally" in f.result.notes

    # populated validation (the 'passed with empty validation' hole is closed)
    ms = m.milestones["MS01"]
    assert ms.validation, "external completion must run a real scrutiny validation"
    assert ms.validation[-1].kind == "scrutiny"

    evs = [e for e in store.read_events(git_repo, m.id) if e["type"] == "FEATURE_COMPLETED"]
    assert evs[-1]["payload"]["external"] is True
    assert evs[-1]["payload"]["commands"][0]["exit_code"] == 0
    assert evs[-1]["payload"]["validation"]["passed"] is True

    # normal milestone flow continues
    ctl.run(m.id)
    m = ctl._get(m.id)
    assert m.milestones["MS01"].status == MilestoneStatus.PASSED


def test_external_completion_rejects_phantom_commit(git_repo):
    ctl = make_controller(git_repo, git=True)
    m = ctl.create_mission("goal")
    ctl.plan(m.id, features=[F("F001", "t")], milestones=[MS("MS01", "o")])
    ctl.approve(m.id)
    try:
        ctl.complete_feature_external(m.id, "F001", "trust me", commit="0" * 40)
        raise AssertionError("phantom commit must be rejected")
    except ValueError as e:
        assert "not found" in str(e)
    assert ctl._get(m.id).features["F001"].status != FeatureStatus.COMPLETED
