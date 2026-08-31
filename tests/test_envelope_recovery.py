"""H7/H9: envelope-missing-but-committed work is classified ENVELOPE_FAILURE
(retryable, cheap), accepted on git evidence when the budget is exhausted, and
workers are told not to leave scratch files (which are cleaned up anyway)."""
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from harness import F, MS, create_and_plan, init_git, make_controller  # noqa: E402

from hamgoose import store  # noqa: E402
from hamgoose.config import Config  # noqa: E402
from hamgoose.models import FailureClass, Feature, FeatureStatus, Milestone, Mission, MissionStatus, RETRYABLE_FAILURES  # noqa: E402
from hamgoose.git import GitManager  # noqa: E402
from hamgoose.controller import MissionController  # noqa: E402
from hamgoose.prompting import worker_prompt  # noqa: E402
from hamgoose.worker import WorkerResult  # noqa: E402


def _feature():
    return Feature(id="F001", title="t", milestone="MS01")


def test_envelope_failure_is_retryable_class():
    assert FailureClass.ENVELOPE_FAILURE in RETRYABLE_FAILURES


def test_unparseable_output_with_branch_work_is_envelope_failure(git_repo):
    """F002/F004/F005 pattern: the model committed the work and never emitted
    the envelope. Git evidence must reclassify this away from
    IMPLEMENTATION_FAILURE."""
    gm = GitManager(git_repo)
    base_branch = "mission/base"
    gm.create_branch(base_branch, "HEAD")
    gm.create_branch("mission/F001", base_branch)

    from hamgoose.worker import parse_worker_output

    res = parse_worker_output("the model wandered off without JSON", run_id="W1", backend="test")
    res.exit_code = 0

    ctl = MissionController(git_repo, Config(git={"enabled": True, "use_worktrees": False}))
    cls = ctl._classify(_feature(), res, changed=False, conflict=False,
                        duration=30.0, worker_timeout=900,
                        worktree_commits=2, worktree_files=5)
    assert cls == FailureClass.ENVELOPE_FAILURE


def test_unparseable_output_without_work_stays_implementation_failure():
    ctl = make_controller(os.devnull)
    from hamgoose.worker import parse_worker_output

    res = parse_worker_output("no json here", run_id="W1", backend="test")
    res.exit_code = 0
    cls = ctl._classify(_feature(), res, changed=False, conflict=False,
                        duration=30.0, worker_timeout=900,
                        worktree_commits=0, worktree_files=0)
    assert cls == FailureClass.IMPLEMENTATION_FAILURE


def test_envelope_retry_prompt_does_not_redo_work():
    f = _feature()
    f.failure = FailureClass.ENVELOPE_FAILURE.value
    f.failure_detail = "previous run detail"
    f.attempts = 1
    m = Mission(id="M1", goal="g", repo=".")
    prompt = worker_prompt(m, f, {}, "")
    assert "ENVELOPE FAILURE" in prompt
    assert "already WROTE AND COMMITTED" in prompt
    assert "Do NOT redo or rewrite the work" in prompt


def test_exhausted_envelope_failure_accepts_work_on_git_evidence(git_repo):
    """The last attempt must not be burned on a missing envelope when the work
    demonstrably exists on the branch (DISCREPANCIES entry 7)."""
    gm = GitManager(git_repo)
    base_branch = "mission/base"
    gm.create_branch(base_branch, "HEAD")
    gm.create_branch("mission/F001", base_branch)
    # real work ON the branch: this is the git evidence the classifier reads
    subprocess.run(["git", "checkout", "-q", "mission/F001"], cwd=git_repo, check=True,
                   capture_output=True)
    with open(os.path.join(git_repo, "impl.py"), "w") as fh:
        fh.write("work\n")
    subprocess.run(["git", "add", "-A"], cwd=git_repo, check=True, capture_output=True)
    gm.commit("feat(F001): impl", cwd=git_repo)

    m = Mission(id="M1", goal="g", repo=git_repo, status=MissionStatus.RUNNING)
    f = Feature(id="F001", title="t", milestone="MS01", status=FeatureStatus.RUNNING,
                branch="mission/F001", workdir=git_repo, attempts=2, max_attempts=3)
    f.worker.started_at = store.utcnow()
    m.features["F001"] = f
    m.milestones["MS01"] = Milestone(id="MS01", objective="o", features=["F001"])
    m.base_commit = gm.base_commit()
    store.save_mission(m)

    ctl = MissionController(git_repo, Config(git={"enabled": True, "use_worktrees": False}))
    res = WorkerResult(status="unknown", raw="no envelope - model stopped early",
                       run_id="W-ENV1", backend="test", exit_code=0)
    ctl._reconcile_result(m, f, res, ctl._cfg(m))

    assert f.status == FeatureStatus.COMPLETED
    notes = " ".join(f.result.notes)
    assert "ENVELOPE_FAILURE" in notes and "git evidence" in notes
    evs = [e for e in store.read_events(git_repo, "M1")
           if e["type"] == "FEATURE_COMPLETED"]
    assert evs and evs[-1]["payload"].get("accepted_on_git_evidence") is True


def test_scratch_files_cleaned_before_reconcile_commit(git_repo):
    """H9: root-level untracked scratch junk is stripped BEFORE the reconcile
    commit (never enters history); tracked work and nested files are kept."""
    gm = GitManager(git_repo)
    base_branch = "mission/base"
    gm.create_branch(base_branch, "HEAD")
    wt = os.path.join(git_repo, "..", "wt_F001")
    gm.add_worktree(wt, "mission/F001", create=True, base_ref=base_branch)

    # committed work in the worktree
    with open(os.path.join(wt, "impl.py"), "w") as fh:
        fh.write("work\n")
    subprocess.run(["git", "add", "-A"], cwd=wt, check=True, capture_output=True)
    gm.commit("feat(F001): impl", cwd=wt)

    # untracked scratch junk at the worktree root + files that must survive
    for name in ("_f001_probe.py", "_f001_out.diff", "_tb.txt", "keep_me.py", "src/_nested.py"):
        path = os.path.join(wt, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write("scratch\n")

    m = Mission(id="M1", goal="g", repo=git_repo, status=MissionStatus.RUNNING)
    f = Feature(id="F001", title="t", milestone="MS01", status=FeatureStatus.RUNNING,
                branch="mission/F001", workdir=wt)
    f.worker.started_at = store.utcnow()
    m.features["F001"] = f
    m.milestones["MS01"] = Milestone(id="MS01", objective="o", features=["F001"])
    m.base_commit = gm.base_commit()
    store.save_mission(m)

    ctl = MissionController(git_repo, Config(git={"enabled": True, "use_worktrees": True}))
    res = WorkerResult(status="completed", summary="done", changed_files=["impl.py"],
                       raw="done", run_id="W-SC1", backend="test", exit_code=0)
    ctl._reconcile_result(m, f, res, ctl._cfg(m))

    assert f.status == FeatureStatus.COMPLETED
    assert not os.path.exists(os.path.join(wt, "_f001_probe.py"))
    assert not os.path.exists(os.path.join(wt, "_f001_out.diff"))
    assert not os.path.exists(os.path.join(wt, "_tb.txt"))
    assert os.path.exists(os.path.join(wt, "keep_me.py"))  # not scratch-shaped
    assert os.path.exists(os.path.join(wt, "src", "_nested.py"))  # nested: untouched
    evs = [e for e in store.read_events(git_repo, "M1") if e["type"] == "SCRATCH_CLEANED"]
    assert evs
    assert set(evs[-1]["payload"]["removed"]) == {"_f001_probe.py", "_f001_out.diff", "_tb.txt"}
    # scratch never entered the mission history
    log = subprocess.run(["git", "log", "--name-only", "--pretty=format:",
                          "mission/base..mission/F001"], cwd=git_repo,
                         capture_output=True, text=True).stdout.split()
    assert "_f001_probe.py" not in log

def test_worker_prompt_forbids_scratch_files():
    m = Mission(id="M1", goal="g", repo=".")
    prompt = worker_prompt(m, _feature(), {}, "")
    assert "NEVER create scratch/debug files inside the repository" in prompt
    assert "system temp directory" in prompt


def test_worktree_evidence_counts_commits_and_files(git_repo):
    gm = GitManager(git_repo)
    base_branch = "mission/base"
    gm.create_branch(base_branch, "HEAD")
    gm.create_branch("mission/F001", base_branch)
    subprocess.run(["git", "checkout", "-q", "mission/F001"], cwd=git_repo, check=True,
                   capture_output=True)
    with open(os.path.join(git_repo, "w.txt"), "w") as fh:
        fh.write("work\n")
    subprocess.run(["git", "add", "-A"], cwd=git_repo, check=True, capture_output=True)
    gm.commit("feat(F001): work", cwd=git_repo)

    m = Mission(id="M1", goal="g", repo=git_repo, status=MissionStatus.RUNNING)
    f = Feature(id="F001", title="t", milestone="MS01", branch="mission/F001", workdir=git_repo)
    m.features["F001"] = f

    ctl = MissionController(git_repo, Config(git={"enabled": True, "use_worktrees": False}))
    commits, files = ctl._worktree_evidence(m, f, ctl._cfg(m))
    assert commits == 1
    assert files == 1
