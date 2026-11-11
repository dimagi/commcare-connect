import pytest

from deploy.tests.git_helpers import commit, push, run_git


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A clone with a local bare `origin`, isolated from the developer's git config."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for key, value in {
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }.items():
        monkeypatch.setenv(key, value)
    origin = tmp_path / "origin.git"
    clone = tmp_path / "clone"
    run_git(tmp_path, "init", "--quiet", "--bare", "-b", "main", str(origin))
    run_git(tmp_path, "init", "--quiet", "-b", "main", str(clone))
    run_git(clone, "remote", "add", "origin", str(origin))
    commit(clone, filename="base.txt")
    push(clone, "main")
    return clone
