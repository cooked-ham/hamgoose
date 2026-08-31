"""HG-12: feature.commits is curated - junk hashes are pruned and recorded."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from harness import init_git  # noqa: E402

from hamgoose import store  # noqa: E402
from hamgoose.git import GitManager  # noqa: E402
from hamgoose.models import Feature, FeatureStatus, Milestone, Mission, MissionStatus  # noqa: E402


def test_prune_commits_drops_and_reports(tmp_path):
    m = Mission(id="M1", goal="g", repo=str(tmp_path), status=MissionStatus.RUNNING)
    m.features["F001"] = Feature(id="F001", title="t", milestone="MS01",
                                 status=FeatureStatus.COMPLETED,
                                 commits=["aaa111", "bbb222", "junk000"])
    m.milestones["MS01"] = Milestone(id="MS01", objective="o", features=["F001"])
    dropped = store.prune_commits(m, "F001", keep={"aaa111", "bbb222"})
    assert dropped == ["junk000"]
    assert m.features["F001"].commits == ["aaa111", "bbb222"]
    assert store.prune_commits(m, "F999", keep=set()) == []  # unknown feature: no-op


def test_controller_curates_on_completion(git_repo, tmp_path):
    """Full path: completion keeps only commits reachable from the branch tip;
    the FEATURE_COMPLETED payload lists what was dropped."""
    import subprocess

    gm = GitManager(git_repo)
    base_branch = "mission/base"
    gm.create_branch(base_branch, "HEAD")
    gm.create_branch("mission/F001", base_branch)
    subprocess.run(["git", "checkout", "-q", "mission/F001"], cwd=git_repo, check=True,
                   capture_output=True)
    with open(os.path.join(git_repo, "f1.txt"), "w") as f:
        f.write("work\n")
    subprocess.run(["git", "add", "-A"], cwd=git_repo, check=True, capture_output=True)
    real = gm.commit("feat(F001): t", cwd=git_repo)

    m = Mission(id="M1", goal="g", repo=git_repo, status=MissionStatus.RUNNING)
    f = Feature(id="F001", title="t", milestone="MS01", status=FeatureStatus.RUNNING,
                commits=[real, "ba91670"], branch="mission/F001", workdir=git_repo)
    f.worker.started_at = store.utcnow()
    m.features["F001"] = f
    m.milestones["MS01"] = Milestone(id="MS01", objective="o", features=["F001"])
    m.base_commit = gm.base_commit()
    store.save_mission(m)

    from hamgoose.config import Config
    from hamgoose.controller import MissionController
    from hamgoose.worker import WorkerResult

    ctl = MissionController(git_repo, Config(git={"enabled": True, "use_worktrees": False}))
    res = WorkerResult(status="completed", summary="done", changed_files=["f1.txt"],
                       raw="done", run_id="W-CUR1", backend="test", exit_code=0)
    ctl._reconcile_result(m, f, res, ctl._cfg(m))

    assert f.status == FeatureStatus.COMPLETED
    # junk ba91670 pruned; the real feature commit survives
    assert "ba91670" not in f.commits and real in f.commits
    evs = [e for e in store.read_events(git_repo, "M1") if e["type"] == "FEATURE_COMPLETED"]
    assert evs and evs[-1]["payload"].get("dropped") == ["ba91670"]