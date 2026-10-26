"""Decide whether the scheduled production deploy should go ahead.

Run by .github/workflows/scheduled-deploy.yml. Deploys only when main has changed since the latest release and CI
passed on it; otherwise skips, and asks for a Slack warning when the skip needs a person's attention.
"""

import argparse
import json
import os
import subprocess
from dataclasses import dataclass

# ci.yml has paths-ignore: ['docs/**'], so commits touching only these get no CI run of their own
CI_IGNORED_PREFIXES = ("docs/",)


@dataclass(frozen=True)
class GateDecision:
    deploy: bool
    warn: bool
    reason: str


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    args = parser.parse_args()

    head_sha = _gh("api", f"repos/{args.repo}/commits/main", "--jq", ".sha")
    release_sha = _latest_release_sha(args.repo)
    ci_run = _latest_ci_run(args.repo)
    paths = []
    if ci_run and ci_run["headSha"] != head_sha:
        compare = f"repos/{args.repo}/compare/{ci_run['headSha']}...{head_sha}"
        paths = _gh("api", compare, "--jq", ".files[].filename").splitlines()

    decision = decide(head_sha, release_sha, ci_run, paths)
    print(decision.reason or f"Deploying {head_sha[:7]}")
    _write_outputs(decision)


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
    if not all(path.startswith(CI_IGNORED_PREFIXES) for path in paths_since_ci_run):
        return _skip(f"CI has not been run on main ({short_sha}); latest run is for an older commit: {run_url}")
    return GateDecision(deploy=True, warn=False, reason="")


def _skip(reason: str) -> GateDecision:
    return GateDecision(deploy=False, warn=True, reason=reason)


def _latest_release_sha(repo: str) -> str | None:
    try:
        tag = _gh("release", "view", "--repo", repo, "--json", "tagName", "--jq", ".tagName")
    except subprocess.CalledProcessError:
        return None
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


def _gh(*args: str) -> str:
    result = subprocess.run(["gh", *args], check=True, capture_output=True, text=True, timeout=60)
    return result.stdout.strip()


def _write_outputs(decision: GateDecision):
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return
    with open(output_path, "a") as f:
        f.write(f"deploy={str(decision.deploy).lower()}\n")
        f.write(f"warn={str(decision.warn).lower()}\n")
        f.write(f"reason={decision.reason}\n")


if __name__ == "__main__":
    main()
