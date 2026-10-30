"""Decide whether the scheduled production deploy should go ahead.

Run by .github/workflows/scheduled-deploy.yml. Deploys only when main has changed since the latest release and CI
passed on it; otherwise skips, and asks for a Slack warning when the skip needs a person's attention.

With --recheck SHA it is run again by deploy.yml once the deploy holds the production concurrency slot, because a
manual deploy may have shipped the same or a newer commit while the scheduled one waited.
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
class GateDecision:
    deploy: bool
    warn: bool
    reason: str


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--recheck", metavar="SHA", help="only check SHA is still ahead of the latest release")
    args = parser.parse_args()

    if args.recheck:
        decision = _recheck(args.repo, args.recheck)
        sha = args.recheck
    else:
        decision, sha = _gate(args.repo)
    print(decision.reason or f"Deploying {sha[:7]}")
    _write_outputs(decision, sha)


def decide(head_sha: str, release_sha: str | None, ci_run: dict | None, paths_since_ci_run: list[str]) -> GateDecision:
    short_sha = head_sha[:7]
    if head_sha == release_sha:
        return GateDecision(deploy=False, warn=False, reason=f"nothing to deploy: {short_sha} is already released")
    if ci_run is None:
        return _skip(f"no CI run found for main ({short_sha})")

    run_url = ci_run["url"]
    if ci_run["status"] != "completed":
        return _skip(f"CI on main ({short_sha}) is {ci_run['status']}: {run_url}")
    if ci_run["conclusion"] != "success":
        return _skip(f"CI on main ({short_sha}) finished with {ci_run['conclusion']}: {run_url}")
    if len(paths_since_ci_run) >= COMPARE_FILE_LIMIT:
        return _skip(f"too many files changed on main ({short_sha}) since the latest CI run to check: {run_url}")
    # A run for a different commit with no files between them means main moved while we looked: not verified
    run_is_for_other_commit = ci_run["headSha"] != head_sha and not paths_since_ci_run
    if run_is_for_other_commit or not all(path.startswith(CI_IGNORED_PREFIXES) for path in paths_since_ci_run):
        return _skip(f"CI has not been run on main ({short_sha}); latest run is for an older commit: {run_url}")
    return GateDecision(deploy=True, warn=False, reason="")


def recheck(sha: str, compare_status: str) -> GateDecision:
    """compare_status is the compare API's status for latest-release...sha."""
    if compare_status == "ahead":
        return GateDecision(deploy=True, warn=False, reason="")
    return GateDecision(
        deploy=False,
        warn=False,
        reason=f"nothing to deploy: {sha[:7]} is {compare_status} compared to the latest release",
    )


def latest_release_sha(repo: str) -> str | None:
    try:
        tag = _gh("api", f"repos/{repo}/releases/latest", "--jq", ".tag_name")
    except subprocess.CalledProcessError as e:
        if "HTTP 404" in (e.stderr or ""):
            return None
        raise
    return _gh("api", f"repos/{repo}/commits/{tag}", "--jq", ".sha")


def _gate(repo: str) -> tuple[GateDecision, str]:
    head_sha = _gh("api", f"repos/{repo}/commits/main", "--jq", ".sha")
    release_sha = latest_release_sha(repo)
    ci_run = _latest_ci_run(repo)
    paths = []
    if ci_run and ci_run["headSha"] != head_sha:
        compare = f"repos/{repo}/compare/{ci_run['headSha']}...{head_sha}"
        # A rename's old path counts too: moving code into docs/ still changes code
        paths = _gh("api", compare, "--jq", ".files[] | .filename, (.previous_filename // empty)").splitlines()
    return decide(head_sha, release_sha, ci_run, paths), head_sha


def _recheck(repo: str, sha: str) -> GateDecision:
    release_sha = latest_release_sha(repo)
    if release_sha is None:
        return GateDecision(deploy=True, warn=False, reason="")
    status = _gh("api", f"repos/{repo}/compare/{release_sha}...{sha}", "--jq", ".status")
    return recheck(sha, status)


def _skip(reason: str) -> GateDecision:
    return GateDecision(deploy=False, warn=True, reason=reason)


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


def _gh(*args: str) -> str:
    result = subprocess.run(["gh", *args], check=True, capture_output=True, text=True, timeout=60)
    return result.stdout.strip()


def _write_outputs(decision: GateDecision, sha: str):
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return
    with open(output_path, "a") as f:
        f.write(f"deploy={str(decision.deploy).lower()}\n")
        f.write(f"warn={str(decision.warn).lower()}\n")
        f.write(f"reason={decision.reason}\n")
        # The deploy checks out this commit, so anything merged after the gate ran waits for the next slot
        f.write(f"sha={sha}\n")


if __name__ == "__main__":
    main()
