"""Add a branch to Connect staging. The logic behind `inv staging-add`.

The staging branch list lives in dimagi/staging-branches. Adding to it goes through the
add-connect-branch.yml workflow there, which then starts the rebuild and deploy.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml

TRUNK = "main"
CODE_REPO = "dimagi/commcare-connect"
CONFIG_REPO = "dimagi/staging-branches"
CONFIG_FILE = "commcare-connect-staging.yml"
ADD_WORKFLOW = "add-connect-branch.yml"

COMMENTED_ENTRY = re.compile(r"^\s*#\s*-\s*(\S+)", flags=re.MULTILINE)
ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")
FAILED_MERGE = re.compile(r"\] (\S+) => ")
NOT_FOUND = re.compile(r"\] (\S+) NOT FOUND")


class StagingAddError(Exception):
    """A step of `inv staging-add` failed; the message says why."""

    pass


@dataclass
class StagingConfig:
    """The branch lists in commcare-connect-staging.yml."""

    branches: list[str]
    commented_branches: list[str]

    @classmethod
    def parse(cls, text):
        """Read the active and commented-out branches from the file's text."""
        data = yaml.safe_load(text) or {}
        return cls(branches=list(data.get("branches") or []), commented_branches=COMMENTED_ENTRY.findall(text))


@dataclass
class TrialResult:
    """The outcome of one git-build-branch trial build."""

    succeeded: bool
    failures: set[str] = field(default_factory=set)
    output: str = ""

    @property
    def failed_unexpectedly(self):
        """The build failed, but not because of a conflict or a missing branch."""
        return not self.succeeded and not self.failures


# Reading state


def current_branch(repo):
    """The branch checked out in `repo`, or "" when HEAD is detached."""
    return git(repo, "branch", "--show-current")


def fetch_origin(repo):
    """Update origin's branches, dropping ones deleted on GitHub."""
    git(repo, "fetch", "--quiet", "--prune", "origin")


def unpushed_commit_count(repo, branch):
    """Commits on the local branch that aren't on origin, or 0 if there is no local branch."""
    if not ref_exists(repo=repo, ref=f"refs/heads/{branch}"):
        return 0
    return int(git(repo, "rev-list", "--count", f"origin/{branch}..refs/heads/{branch}"))


def fetch_config():
    """The current branch list from dimagi/staging-branches."""
    text = run(
        ["gh", "api", f"repos/{CONFIG_REPO}/contents/{CONFIG_FILE}", "-H", "Accept: application/vnd.github.raw"]
    )
    return StagingConfig.parse(text)


def open_pr_bases():
    """Map each open PR's branch to its base branch, for PRs not based on the trunk."""
    try:
        output = run(
            [
                "gh",
                "pr",
                "list",
                "--repo",
                CODE_REPO,
                "--state",
                "open",
                "--limit",
                "1000",
                "--json",
                "headRefName,baseRefName",
            ]
        )
    except StagingAddError as error:
        raise StagingAddError(
            f"Couldn't list open PRs, which is needed to place a branch stacked on another.\n{error}"
        ) from error
    return {pr["headRefName"]: pr["baseRefName"] for pr in json.loads(output) if pr["baseRefName"] != TRUNK}


# Position


def find_insert_before(*, repo, branches, new_branch, pr_bases):
    """Return the listed branch to insert the new branch before, or None to add it at the end.

    A branch that contains two or more listed branches is a compatibility branch. It goes just
    before the first of them, so that they merge as no-ops after it.
    """
    contained = contained_branches(repo=repo, branches=branches, new_branch=new_branch, pr_bases=pr_bases)
    if len(contained) < 2:
        return None
    return contained[0]


def contained_branches(*, repo, branches, new_branch, pr_bases):
    """Listed branches the new branch contains, in list order.

    The new branch contains X if it has at least one of X's own commits, so X is still found
    after X gets new commits. A branch the new branch only contains because it was cut from
    another contained branch (a stack) is left out.
    """
    candidates = [
        branch
        for branch in branches
        if branch not in (TRUNK, new_branch) and ":" not in branch and ref_exists(repo=repo, ref=origin_ref(branch))
    ]
    bases = {
        branch: bases_of(repo=repo, branch=branch, candidates=candidates, pr_bases=pr_bases) for branch in candidates
    }
    contained = [
        branch
        for branch in candidates
        if contains_any(repo=repo, ref=origin_ref(new_branch), commits_of=origin_ref(branch), excluding=bases[branch])
    ]
    return [branch for branch in contained if not any(branch in bases[other] for other in contained)]


def bases_of(*, repo, branch, candidates, pr_bases):
    """Branches that `branch` was cut from.

    These are listed branches whose tip `branch` contains, plus the chain of open PR bases. The
    PR base is needed when the base got new commits after `branch` was cut from it, because git
    history alone can't tell which of the two was cut from the other.
    """
    bases = {
        other
        for other in candidates
        if other != branch
        and is_ancestor(repo=repo, ancestor=origin_ref(other), descendant=origin_ref(branch))
        and not is_ancestor(repo=repo, ancestor=origin_ref(branch), descendant=origin_ref(other))
    }
    base = pr_bases.get(branch)
    while base and base != TRUNK and base not in bases and ref_exists(repo=repo, ref=origin_ref(base)):
        bases.add(base)
        base = pr_bases.get(base)
    return bases


def contains_any(*, repo, ref, commits_of, excluding):
    """Whether `ref` has at least one of the commits on `commits_of` that aren't on the trunk or `excluding`."""
    exclude = [origin_ref(TRUNK), *(origin_ref(branch) for branch in excluding)]
    own = int(git(repo, "rev-list", "--count", commits_of, "--not", *exclude))
    own_missing_from_ref = int(git(repo, "rev-list", "--count", commits_of, "--not", *exclude, ref))
    return own_missing_from_ref < own


# Trial build


def branches_with(*, branches, new_branch, before):
    """The list with `new_branch` added before `before`, or at the end if `before` is None."""
    if before is None:
        return [*branches, new_branch]
    index = branches.index(before)
    return [*branches[:index], new_branch, *branches[index:]]


def trial_added_failures(*, repo, branches, proposed_branches):
    """Build both lists and return (failures only the new list has, result for the new list).

    Comparing with the current list means a staging that is already broken doesn't block adding
    a branch that adds no problems of its own.
    """
    current = run_trial(repo=repo, branches=branches)
    proposed = run_trial(repo=repo, branches=proposed_branches)
    return proposed.failures - current.failures, proposed


def run_trial(*, repo, branches):
    """Build the branch list in a temporary worktree without pushing anything."""
    missing = {
        branch for branch in branches if ":" not in branch and not ref_exists(repo=repo, ref=origin_ref(branch))
    }
    name = f"staging-trial-{uuid.uuid4().hex[:8]}"
    with tempfile.TemporaryDirectory(prefix="staging-trial-") as tmp:
        worktree = Path(tmp) / "worktree"
        config_path = Path(tmp) / CONFIG_FILE
        config_path.write_text(trial_config(branches=[b for b in branches if b not in missing], name=name))
        git(repo, "worktree", "add", "--quiet", "--detach", str(worktree), origin_ref(TRUNK))
        try:
            completed = subprocess.run(
                [git_build_branch_command(), str(config_path), "fetch", "rebuild", "--path", str(worktree)],
                cwd=worktree,
                env=env_without_git_hooks(),
                capture_output=True,
                text=True,
            )
        finally:
            git(repo, "worktree", "remove", "--force", str(worktree))
            if ref_exists(repo=repo, ref=f"refs/heads/{name}"):
                git(repo, "branch", "--quiet", "-D", name)
    output = ANSI_ESCAPE.sub("", completed.stdout + completed.stderr)
    failures = parse_failures(output) | missing
    return TrialResult(succeeded=completed.returncode == 0 and not missing, failures=failures, output=output)


def trial_config(*, branches, name):
    """A copy of the config that builds from origin into a throwaway branch.

    git-build-branch prefers a local branch over origin/<branch>, and local branches can be stale,
    so plain entries become `origin:<branch>`. Fork entries (`owner:branch`) are left alone.
    """
    entries = [branch if ":" in branch else f"origin:{branch}" for branch in branches]
    return yaml.safe_dump({"trunk": TRUNK, "name": name, "branches": entries}, sort_keys=False)


def parse_failures(output):
    """Branches that failed to merge or weren't found, from git-build-branch's output."""
    names = FAILED_MERGE.findall(output) + NOT_FOUND.findall(output)
    return {normalize_entry(name) for name in names}


def normalize_entry(name):
    """Strip the `origin/` or `origin:` prefix git-build-branch puts on branch names."""
    for prefix in ("origin/", "origin:"):
        if name.startswith(prefix):
            return name.removeprefix(prefix)
    return name


def failure_details(output):
    """The lines of a trial build's output that explain its failures."""
    keep = ("conflicts with", "NOT FOUND", "=> ", "FAIL", "may have been merged", "No merge commit")
    return "\n".join(line for line in output.splitlines() if any(marker in line for marker in keep))


def git_build_branch_command():
    """The git-build-branch in this virtualenv if there is one, else the one on PATH."""
    in_venv = Path(sys.executable).parent / "git-build-branch"
    return str(in_venv) if in_venv.exists() else "git-build-branch"


def env_without_git_hooks():
    """Stop the trial's merge commits from running the developer's git hooks."""
    return {
        **os.environ,
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "core.hooksPath",
        "GIT_CONFIG_VALUE_0": "/dev/null",
    }


# Adding


def start_add_workflow(*, branch, before):
    """Run add-connect-branch.yml and return its run ID."""
    user = run(["gh", "api", "user", "--jq", ".login"])
    existing_ids = {r["databaseId"] for r in list_add_runs(user=user)}
    started_at = datetime.now(tz=UTC) - timedelta(seconds=10)  # allow for clock differences
    command = ["gh", "workflow", "run", ADD_WORKFLOW, "--repo", CONFIG_REPO, "-f", f"branch={branch}"]
    if before:
        command += ["-f", f"before={before}"]
    run(command)
    return find_new_run(started_at=started_at, user=user, existing_ids=existing_ids)


def find_new_run(*, started_at, user, existing_ids, attempts=15):
    """`gh workflow run` doesn't print the run ID, so wait for a run of the user's that wasn't there before."""
    for _ in range(attempts):
        runs = [
            r
            for r in list_add_runs(user=user)
            if r["databaseId"] not in existing_ids and datetime.fromisoformat(r["createdAt"]) >= started_at
        ]
        if runs:
            return max(runs, key=lambda r: r["createdAt"])["databaseId"]
        time.sleep(2)
    raise StagingAddError(
        f"Started {ADD_WORKFLOW} but couldn't find the run. Check https://github.com/{CONFIG_REPO}/actions"
    )


def list_add_runs(*, user):
    """The user's most recent dispatched runs of the add workflow."""
    output = run(
        [
            "gh",
            "run",
            "list",
            "--repo",
            CONFIG_REPO,
            "--workflow",
            ADD_WORKFLOW,
            "--user",
            user,
            "--event",
            "workflow_dispatch",
            "--limit",
            "10",
            "--json",
            "databaseId,createdAt",
        ]
    )
    return json.loads(output)


def watch_run(run_id):
    """Show the run's progress until it finishes. Returns whether it succeeded."""
    command = ["gh", "run", "watch", str(run_id), "--repo", CONFIG_REPO, "--exit-status"]
    return subprocess.run(command).returncode == 0


# Helpers


def origin_ref(branch):
    """The remote-tracking ref for a branch on origin."""
    return f"refs/remotes/origin/{branch}"


def ref_exists(repo, ref):
    """Whether `ref` exists in `repo`."""
    return subprocess.run(["git", "show-ref", "--verify", "--quiet", ref], cwd=repo).returncode == 0


def is_ancestor(repo, ancestor, descendant):
    """Whether `ancestor` is in the history of `descendant`."""
    return subprocess.run(["git", "merge-base", "--is-ancestor", ancestor, descendant], cwd=repo).returncode == 0


def git(repo, *args):
    """Run a git command in `repo` and return its output."""
    return run(["git", *args], cwd=repo)


def run(command, cwd=None):
    """Run a command and return its output, raising StagingAddError if it fails."""
    completed = subprocess.run(command, cwd=cwd, capture_output=True, text=True)
    if completed.returncode != 0:
        raise StagingAddError(f"`{' '.join(command)}` failed:\n{completed.stderr.strip()}")
    return completed.stdout.strip()
