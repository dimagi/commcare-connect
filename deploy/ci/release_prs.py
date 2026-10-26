"""Print the numbers of the PRs merged into main between two release tags, one per line.

Run by .github/workflows/release-notes.yml. Working from the commits between the tags, rather than merge dates,
keeps each PR in the release that actually shipped it: a PR merged while a deploy is running belongs to the next one.
"""

import argparse
import json
import subprocess
from collections.abc import Iterable


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--prev-tag", required=True)
    parser.add_argument("--tag", required=True)
    args = parser.parse_args()

    shas = _gh(
        "api", "--paginate", f"repos/{args.repo}/compare/{args.prev_tag}...{args.tag}", "--jq", ".commits[].sha"
    ).split()
    commit_pulls = [json.loads(_gh("api", f"repos/{args.repo}/commits/{sha}/pulls")) for sha in shas]
    for number in collect_pr_numbers(commit_pulls):
        print(number)


def collect_pr_numbers(commit_pulls: Iterable[list[dict]], base: str = "main") -> list[int]:
    """commit_pulls holds the commits/{sha}/pulls response for each commit in the range."""
    numbers = {
        pull["number"] for pulls in commit_pulls for pull in pulls if pull["merged_at"] and pull["base"]["ref"] == base
    }
    return sorted(numbers)


def _gh(*args: str) -> str:
    result = subprocess.run(["gh", *args], check=True, capture_output=True, text=True, timeout=120)
    return result.stdout.strip()


if __name__ == "__main__":
    main()
