"""Build small git repos for the staging tests."""

import subprocess


def run_git(cwd, *args):
    """Run git in `cwd`, raising if it fails."""
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def commit(repo, *, filename, content="change\n"):
    """Write `content` to `filename` and commit it."""
    path = repo / filename
    path.parent.mkdir(parents=True, exist_ok=True)
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
    """Commit one more file to an existing branch and push it."""
    run_git(repo, "checkout", "--quiet", name)
    commit(repo, filename=filename)
    push(repo, name)
    run_git(repo, "checkout", "--quiet", "main")


def push(repo, *branches):
    """Push the branches and fetch, so origin's refs are up to date."""
    run_git(repo, "push", "--quiet", "origin", *branches)
    run_git(repo, "fetch", "--quiet", "origin")
