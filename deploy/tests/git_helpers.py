"""Build small git repos for the staging tests."""

import subprocess


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
