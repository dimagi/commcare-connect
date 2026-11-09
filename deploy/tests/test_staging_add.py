import subprocess

import pytest

from deploy import staging_add as staging


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


def run_git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def commit(repo, *, filename, content="change\n"):
    path = repo / filename
    path.write_text(content)
    run_git(repo, "add", filename)
    run_git(repo, "commit", "--quiet", "-m", f"edit {filename}")


def branch(repo, *, name, start="main", files=()):
    """Create a branch from `start`, commit one file per name in `files`, and push it."""
    run_git(repo, "checkout", "--quiet", "-b", name, start)
    for filename in files or [f"{name.replace('/', '_')}.txt"]:
        commit(repo, filename=filename)
    push(repo, name)
    run_git(repo, "checkout", "--quiet", "main")


def merge_branch(repo, *, name, merging):
    """Create a compatibility branch: the first branch with the others merged in."""
    run_git(repo, "checkout", "--quiet", "-b", name, merging[0])
    for other in merging[1:]:
        run_git(repo, "merge", "--quiet", "--no-edit", other)
    push(repo, name)
    run_git(repo, "checkout", "--quiet", "main")


def add_commit_to(repo, *, name, filename):
    run_git(repo, "checkout", "--quiet", name)
    commit(repo, filename=filename)
    push(repo, name)
    run_git(repo, "checkout", "--quiet", "main")


def push(repo, *branches):
    run_git(repo, "push", "--quiet", "origin", *branches)
    run_git(repo, "fetch", "--quiet", "origin")


def find_before(repo, *, branches, new_branch, pr_bases=None):
    return staging.find_insert_before(repo=repo, branches=branches, new_branch=new_branch, pr_bases=pr_bases or {})


class TestFindInsertBefore:
    def test_normal_branch_goes_at_the_end(self, repo):
        branch(repo, name="a")
        branch(repo, name="new")
        assert find_before(repo, branches=["main", "a"], new_branch="new") is None

    def test_branch_built_on_one_listed_branch_goes_at_the_end(self, repo):
        branch(repo, name="a")
        branch(repo, name="new", start="a")
        assert find_before(repo, branches=["main", "a"], new_branch="new") is None

    def test_compatibility_branch_goes_before_the_first_branch_it_contains(self, repo):
        for name in ("a", "b", "c"):
            branch(repo, name=name)
        merge_branch(repo, name="b+c", merging=["b", "c"])
        assert find_before(repo, branches=["main", "a", "b", "c"], new_branch="b+c") == "b"

    def test_compatibility_branch_is_found_after_its_sources_get_new_commits(self, repo):
        branch(repo, name="a")
        branch(repo, name="c")
        merge_branch(repo, name="a+c", merging=["a", "c"])
        add_commit_to(repo, name="a", filename="later_a.txt")
        add_commit_to(repo, name="c", filename="later_c.txt")
        assert find_before(repo, branches=["main", "x", "a", "c"], new_branch="a+c") == "a"

    def test_stacked_branch_on_a_stack_goes_at_the_end(self, repo):
        branch(repo, name="a")
        branch(repo, name="b", start="a")
        branch(repo, name="new", start="b")
        assert find_before(repo, branches=["main", "a", "b"], new_branch="new") is None

    def test_listed_stacked_branch_is_not_matched_through_its_base(self, repo):
        branch(repo, name="a")
        branch(repo, name="b", start="a")
        branch(repo, name="c")
        merge_branch(repo, name="a+c", merging=["a", "c"])
        assert staging.contained_branches(
            repo=repo, branches=["main", "a", "b", "c"], new_branch="a+c", pr_bases={}
        ) == ["a", "c"]

    def test_pr_base_identifies_a_stack_whose_base_moved_on(self, repo):
        branch(repo, name="a")
        branch(repo, name="b", start="a")
        add_commit_to(repo, name="a", filename="later_a.txt")
        branch(repo, name="c")
        merge_branch(repo, name="a+c", merging=["a", "c"])
        branches = ["main", "a", "b", "c"]

        without_pr = staging.contained_branches(repo=repo, branches=branches, new_branch="a+c", pr_bases={})
        with_pr = staging.contained_branches(repo=repo, branches=branches, new_branch="a+c", pr_bases={"b": "a"})

        assert without_pr == ["a", "b", "c"]
        assert with_pr == ["a", "c"]

    def test_ignores_fork_and_missing_entries(self, repo):
        branch(repo, name="a")
        branch(repo, name="c")
        merge_branch(repo, name="a+c", merging=["a", "c"])
        branches = ["main", "someone:fork", "gone", "a", "c"]
        assert find_before(repo, branches=branches, new_branch="a+c") == "a"


class TestStagingConfig:
    def test_parse(self):
        text = (
            "trunk: main\nname: autostaging\nbranches:\n"
            "  - main\n  - a/one  # Someone Nov 1\n  # - b/two # Someone Nov 2\n  #- c/three\n"
            "submodules:\n"
        )
        config = staging.StagingConfig.parse(text)
        assert config.branches == ["main", "a/one"]
        assert config.commented_branches == ["b/two", "c/three"]


@pytest.mark.parametrize(
    "before, expected",
    [
        (None, ["main", "a", "b", "new"]),
        ("a", ["main", "new", "a", "b"]),
    ],
)
def test_branches_with(before, expected):
    assert staging.branches_with(branches=["main", "a", "b"], new_branch="new", before=before) == expected


def test_trial_config_builds_from_origin_into_a_throwaway_branch():
    config = staging.trial_config(branches=["main", "a/one", "someone:fork"], name="staging-trial-x")
    assert config == ("trunk: main\nname: staging-trial-x\nbranches:\n- origin:main\n- origin:a/one\n- someone:fork\n")


def test_parse_failures():
    output = (
        "  [/tmp/w] Merging origin/a into staging-trial-x ok\n"
        "  [/tmp/w] Merging origin/b into staging-trial-x FAIL\n"
        "  [/tmp/w] origin:gone NOT FOUND\n"
        "You must fix the following merge conflicts before rebuilding:\n"
        "\n[/tmp/w] origin/b => staging-trial-x\n"
        "origin/b conflicts with origin/a\n"
    )
    assert staging.parse_failures(output) == {"b", "gone"}


class TestTrialBuild:
    def test_reports_a_conflict_the_new_branch_adds(self, repo):
        branch(repo, name="a", files=["shared.txt"])
        run_git(repo, "checkout", "--quiet", "-b", "new", "main")
        commit(repo, filename="shared.txt", content="different\n")
        push(repo, "new")
        run_git(repo, "checkout", "--quiet", "main")

        added, result = staging.trial_added_failures(
            repo=repo, branches=["main", "a"], new_branches=["main", "a", "new"]
        )

        assert added == {"new"}
        assert not result.succeeded

    def test_ignores_failures_staging_already_has(self, repo):
        branch(repo, name="a", files=["shared.txt"])
        run_git(repo, "checkout", "--quiet", "-b", "b", "main")
        commit(repo, filename="shared.txt", content="different\n")
        push(repo, "b")
        run_git(repo, "checkout", "--quiet", "main")
        branch(repo, name="new")

        added, result = staging.trial_added_failures(
            repo=repo, branches=["main", "a", "b", "gone"], new_branches=["main", "a", "b", "gone", "new"]
        )

        assert added == set()
        assert result.failures == {"b", "gone"}

    def test_leaves_no_branches_or_worktrees_behind(self, repo):
        branch(repo, name="a")
        branches_before = subprocess.run(["git", "branch"], cwd=repo, capture_output=True, text=True).stdout

        result = staging.run_trial(repo=repo, branches=["main", "a"])

        assert result.succeeded, result.output
        assert subprocess.run(["git", "branch"], cwd=repo, capture_output=True, text=True).stdout == branches_before
        worktrees = subprocess.run(["git", "worktree", "list"], cwd=repo, capture_output=True, text=True).stdout
        assert len(worktrees.splitlines()) == 1

    def test_builds_from_origin_not_stale_local_branches(self, repo):
        branch(repo, name="a", files=["shared.txt"])
        # Local `new` conflicts with `a`, but origin/new doesn't
        branch(repo, name="new")
        run_git(repo, "checkout", "--quiet", "new")
        commit(repo, filename="shared.txt", content="local only\n")
        run_git(repo, "checkout", "--quiet", "main")

        result = staging.run_trial(repo=repo, branches=["main", "a", "new"])

        assert result.succeeded, result.output
