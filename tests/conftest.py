import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from harness import init_git


@pytest.fixture
def tmp_repo(tmp_path):
    return str(tmp_path)


@pytest.fixture
def git_repo(tmp_path):
    """A real git repository with an initial commit."""
    repo = str(tmp_path)
    init_git(repo)
    return repo
