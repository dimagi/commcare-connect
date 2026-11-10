"""Decide whether the scheduled production deploy should go ahead.

Run by .github/workflows/scheduled-deploy.yml, whose run holds the production deploy concurrency group from this check
through the deploy, so no other deploy can change what production runs in between.

Deploys only when main is ahead of the latest release and CI passed on it; otherwise skips. Every skip is posted to
Slack, as a warning when it needs a person's attention.
"""

import argparse
import json
import os
import subprocess
from dataclasses import dataclass

# ci.yml has paths-ignore: ['docs/**'], so commits touching only these get no CI run of their own
CI_IGNORED_PREFIXES = ("docs/",)
# The compare API lists at most this many files; a list this long may be missing some
COMPARE_FILE_LIMIT = 300


@dataclass(frozen=True)
class ReadinessDecision:
    deploy: bool
    warn: bool
    reason: str


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    args = parser.parse_args()

    decision, sha = _check_readiness(args.repo)
    print(decision.reason or f"Deploying {sha[:7]}")
    _write_outputs(decision, sha)


def _check_readiness(repo: str) -> tuple[ReadinessDecision, str]:
    head_sha = _gh("api", f"repos/{repo}/commits/main", "--jq", ".sha")
    release_status = _release_status(repo, head_sha)
    if release_status not in (None, "ahead"):
        # Decided already, so a failing CI lookup can't turn a quiet skip into a warning
        return decide(head_sha, release_status, None, []), head_sha
    ci_run = _latest_ci_run(repo)
    paths = []
    if ci_run and ci_run["headSha"] != head_sha:
        compare = f"repos/{repo}/compare/{ci_run['headSha']}...{head_sha}"
        # A rename's old path counts too: moving code into docs/ still changes code
        paths = _gh("api", compare, "--jq", ".files[] | .filename, (.previous_filename // empty)").splitlines()
    return decide(head_sha, release_status, ci_run, paths), head_sha


def decide(
    head_sha: str, release_status: str | None, ci_run: dict | None, paths_since_ci_run: list[str]
) -> ReadinessDecision:
    """Decide whether main's head commit should be deployed.

    release_status is the compare API's status for latest-release...head_sha, or None when there is no release yet.
    Deploys only when head_sha is ahead of the release and CI passed on it. A passing run on an older commit also
    counts, provided paths_since_ci_run (the files changed between that run's commit and head_sha) are all ones CI
    ignores. An already released head is skipped without a warning; every other skip asks for one.
    """
    short_sha = head_sha[:7]
    if release_status == "identical":
        return ReadinessDecision(
            deploy=False, warn=False, reason=f"nothing to deploy: {short_sha} is already released"
        )
    if release_status not in (None, "ahead"):
        # Behind or diverged: the latest release has commits main doesn't, for example after main was reset. Deploying
        # main would drop them, and skipping leaves production on code that isn't on main, so a person decides.
        return _skip(
            f"the latest release isn't in main's history ({short_sha} is {release_status}), so deploying main would"
            " drop commits production has; deploy manually if main should ship"
        )
    if ci_run is None:
        return _skip(f"no CI run found for main ({short_sha})")

    run_url = ci_run["url"]
    if ci_run["status"] != "completed":
        return _skip(f"CI on main ({short_sha}) is {ci_run['status']}: {run_url}")
    if ci_run["conclusion"] != "success":
        return _skip(f"CI on main ({short_sha}) finished with {ci_run['conclusion']}: {run_url}")
    if len(paths_since_ci_run) >= COMPARE_FILE_LIMIT:
        return _skip(
            f"too many files changed on main ({short_sha}) since the latest CI run to check: {run_url}"
            " - please deploy manually"
        )
    # A run for a different commit with no files between them means main moved while we looked: not verified
    run_is_for_other_commit = ci_run["headSha"] != head_sha and not paths_since_ci_run
    if run_is_for_other_commit or not all(path.startswith(CI_IGNORED_PREFIXES) for path in paths_since_ci_run):
        return _skip(f"CI has not been run on main ({short_sha}); latest run is for an older commit: {run_url}")
    return ReadinessDecision(deploy=True, warn=False, reason="")


def _skip(reason: str) -> ReadinessDecision:
    return ReadinessDecision(deploy=False, warn=True, reason=reason)


def _release_status(repo: str, head_sha: str) -> str | None:
    release_sha = latest_release_sha(repo)
    if release_sha is None:
        return None
    return _gh("api", f"repos/{repo}/compare/{release_sha}...{head_sha}", "--jq", ".status")


def latest_release_sha(repo: str) -> str | None:
    try:
        tag = _gh("api", f"repos/{repo}/releases/latest", "--jq", ".tag_name")
    except subprocess.CalledProcessError as e:
        if "HTTP 404" in (e.stderr or ""):
            return None
        raise
    return _gh("api", f"repos/{repo}/commits/{tag}", "--jq", ".sha")


def _latest_ci_run(repo: str) -> dict | None:
    runs = json.loads(
        _gh(
            "run",
            "list",
            "--repo",
            repo,
            "--workflow",
            "ci.yml",
            "--branch",
            "main",
            "--event",
            "push",
            "--limit",
            "1",
            "--json",
            "headSha,status,conclusion,url",
        )
    )
    return runs[0] if runs else None


def _write_outputs(decision: ReadinessDecision, sha: str):
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return
    with open(output_path, "a") as f:
        f.write(f"deploy={str(decision.deploy).lower()}\n")
        f.write(f"warn={str(decision.warn).lower()}\n")
        # One key=value per line: a newline in the reason would end it early and could start another output, such as
        # a second deploy=true that overrides the first
        reason = " ".join(decision.reason.split())
        f.write(f"reason={reason}\n")
        # The deploy checks out this commit, so anything merged after this check waits for the next slot
        f.write(f"sha={sha}\n")


def _gh(*args: str) -> str:
    result = subprocess.run(["gh", *args], check=True, capture_output=True, text=True, timeout=60)
    return result.stdout.strip()


if __name__ == "__main__":
    main()
