"""HG-15: repo-analysis quality - structured digest, paragraph-boundary cuts,
and git is_repo true on plain and worktree layouts."""
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from harness import make_controller  # noqa: E402

from hamgoose.controller import MissionController  # noqa: E402
from hamgoose.git import GitManager  # noqa: E402

README = (
    "# Project\n\nFirst paragraph explains the project purpose.\n\n"
    "Second paragraph has conventions. " * 40 + "\n\nThird paragraph is short.\n"
)


def _mkrepo(tmp_path):
    repo = str(tmp_path)
    os.makedirs(os.path.join(repo, "src", "app"), exist_ok=True)
    os.makedirs(os.path.join(repo, "tests"), exist_ok=True)
    with open(os.path.join(repo, "README.md"), "w", encoding="utf-8") as f:
        f.write(README)
    with open(os.path.join(repo, "src", "app", "main.py"), "w") as f:
        f.write("x = 1\n")
    with open(os.path.join(repo, "PLAN.md"), "w", encoding="utf-8") as f:
        f.write("# Plan\n\nstep one\n\nstep two\n")
    return repo


def test_summary_is_structured_digest(tmp_path):
    repo = _mkrepo(tmp_path)
    ctl = make_controller(repo)
    m = ctl.create_mission("g")
    s = m.repo_analysis["summary"]
    assert "top-level tree" in s
    assert "src/" in s and "tests/" in s
    assert "README.md (head)" in s
    assert "PLAN.md (head)" in s
    assert ".git" not in s.split("README.md")[0]  # junk dirs skipped


def test_summary_varies_with_repo_content(tmp_path, tmp_path_factory):
    a = _mkrepo(tmp_path)
    m1 = make_controller(a).create_mission("g")
    b = str(tmp_path_factory.mktemp("other"))
    os.makedirs(b, exist_ok=True)
    with open(os.path.join(b, "README.md"), "w") as f:
        f.write("# Different\n\nA completely different project.\n")
    m2 = make_controller(b).create_mission("g")
    assert m1.repo_analysis["summary"] != m2.repo_analysis["summary"]


def test_readme_cut_lands_on_paragraph_boundary(tmp_path):
    repo = _mkrepo(tmp_path)
    ctl = make_controller(repo)
    head = ctl._para_cut(README, 500)
    assert head.endswith("[...truncated at paragraph boundary]")
    assert "Third paragraph" not in head[:500] or len(README) <= 500
    m = ctl.create_mission("g")
    # small README fits whole: the digest ends at its natural paragraph end
    assert "Third paragraph is short." in m.repo_analysis["summary"]
    # instructions likewise carry the complete README, cut at a boundary only
    assert "### README.md" in m.repo_analysis["instructions"]


def test_big_readme_digest_truncates_at_boundary(tmp_path):
    repo = _mkrepo(tmp_path)
    big = "# Big\n\n" + ("A paragraph with plenty of words in it.\n\n" * 300)
    with open(os.path.join(repo, "README.md"), "w", encoding="utf-8") as f:
        f.write(big)
    ctl = make_controller(repo)
    m = ctl.create_mission("g")
    s = m.repo_analysis["summary"]
    assert "[...truncated at paragraph boundary]" in s
    head = s.split("README.md (head)\n")[1].split("\n###")[0].strip()
    assert head.endswith("[...truncated at paragraph boundary]")
    # never cut mid-word/mid-phrase: the line before the marker must be whole
    last_line = head.rsplit("\n", 1)[0].rstrip()
    assert last_line and not last_line.endswith((" ", ",", "\"", "'"))


def test_para_cut_full_text_untouched(tmp_path):
    ctl = make_controller(str(tmp_path))
    assert ctl._para_cut("short\n\ntext", 500) == "short\n\ntext"


def test_is_repo_plain_layout(git_repo, tmp_path, monkeypatch):
    assert GitManager(git_repo).is_repo() is True
    # robust even when the process cwd is somewhere else entirely (the observed
    # false-negative mode: analysis ran from a different directory)
    monkeypatch.chdir(str(tmp_path))
    assert GitManager(git_repo).is_repo() is True


def test_is_repo_worktree_layout(git_repo, tmp_path):
    gm = GitManager(git_repo)
    wt = os.path.join(str(tmp_path), "wt")
    gm.create_worktree(wt, "mission/wt-test")
    assert os.path.isdir(wt)
    assert GitManager(wt).is_repo() is True
    gm.remove_worktree(wt)


def test_is_repo_not_a_repo(tmp_path):
    # NOTE: pytest tmp dirs live under this git repo's worktree, so use a
    # directory genuinely outside any repo.
    import tempfile

    outside = tempfile.mkdtemp(prefix="hamgoose_notrepo_")
    assert GitManager(outside).is_repo() is False


def test_analysis_records_is_repo_flag(git_repo):
    ctl = make_controller(git_repo, git=True)
    m = ctl.create_mission("g")
    assert m.repo_analysis["git"]["is_repo"] is True
