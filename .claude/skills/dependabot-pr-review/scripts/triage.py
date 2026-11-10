#!/usr/bin/env python3
"""Triage every open Dependabot PR against the repo's current default branch.

An old Dependabot queue decays in ways the PR diff cannot show. A PR is obsolete
when the package was deleted from main, and -- far more often -- when main has
simply moved PAST the version the PR proposes. Both read as "no problems found"
if you only review the diff, so check the whole queue first and review only what
survives.

Usage:
    python scripts/triage.py [--repo dimagi/commcare-connect]
"""

import argparse
import json
import re
import subprocess

from lockfiles import blob, fetch, parse_npm_lock, parse_uv_lock, remote_for, sh, version_key

SHA_PIN = re.compile(r"^[0-9a-f]{40}$")
# `uses: owner/action@<sha> # v2.21.0` -- the trailing comment is the only
# record of which release a SHA pin is.
USES = re.compile(r"uses:\s*['\"]?([^\s'\"@]+)@([^\s'\"#]+)['\"]?(?:\s*#\s*(\S+))?")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default="dimagi/commcare-connect")
    args = ap.parse_args()

    # Read --repo's default branch, not whatever `origin/main` this checkout has.
    default = sh("gh", "repo", "view", args.repo, "--json", "defaultBranchRef", "-q", ".defaultBranchRef.name").strip()
    main_sha = fetch(remote_for(args.repo), f"refs/heads/{default}")

    python_pkgs = parse_uv_lock(blob(main_sha, "uv.lock"))
    # Dependabot bumps the hoisted copy package.json declares; private copies
    # nested under other packages would otherwise decide the comparison.
    npm_pkgs = parse_npm_lock(blob(main_sha, "package-lock.json"), top_level_only=True)
    actions = workflow_actions(main_sha)

    prs = json.loads(
        sh(
            "gh",
            "pr",
            "list",
            "--repo",
            args.repo,
            "--author",
            "app/dependabot",
            "--state",
            "open",
            "--json",
            "number,title,createdAt",
            "--limit",
            "100",
        )
    )

    rows, review = [], []
    for pr in sorted(prs, key=lambda p: -p["number"]):
        n, title, created = pr["number"], pr["title"], pr["createdAt"][:10]
        m = re.search(r"(?:Bump|Update) ([\w./@-]+) (?:requirement )?from v?([\d.]+) to v?([\d.]+)", title)
        if not m:
            rows.append((n, created, title[:40], "-", "-", "GROUPED - review in full"))
            review.append(n)
            continue

        pkg, old, new = m.groups()
        if pkg.lower() in python_pkgs:
            kind, versions = "py", python_pkgs[pkg.lower()]
        elif pkg in npm_pkgs:
            kind, versions = "npm", npm_pkgs[pkg]
        elif pkg in actions:
            kind, versions = "action", actions[pkg]
        else:
            rows.append(
                (n, created, pkg[:40], f"{old}->{new}", "ABSENT", "OBSOLETE - not on main; confirm deliberate removal")
            )
            continue

        if None in versions:
            # A SHA pin with no version comment: the SHA's digits are not a
            # version, so this cannot be compared automatically.
            rows.append((n, created, pkg[:40], f"{old}->{new}", "SHA PIN", f"REVIEW ({kind}) - unversioned pin"))
            review.append(n)
            continue
        # OBSOLETE only if *every* locked copy / call site is already at or past
        # the target; one lagging copy is still something the PR would move.
        cur = min(versions, key=version_key)
        if version_key(cur) >= version_key(new):
            rows.append((n, created, pkg[:40], f"{old}->{new}", cur, "OBSOLETE - main already at/past target"))
        else:
            rows.append((n, created, pkg[:40], f"{old}->{new}", cur, f"REVIEW ({kind})"))
            review.append(n)

    w = max([len(r[2]) for r in rows] + [10])
    print(f"{'PR':<7}{'opened':<12}{'package':<{w + 2}}{'PR wants':<18}{'on main':<12}status")
    print("-" * (55 + w))
    for n, created, pkg, want, cur, status in rows:
        print(f"#{n:<6}{created:<12}{pkg:<{w + 2}}{want:<18}{str(cur):<12}{status}")

    obsolete = [r[0] for r in rows if r[5].startswith("OBSOLETE")]
    print(f"\n{len(rows)} open | {len(obsolete)} obsolete | {len(review)} need review")
    if obsolete:
        print("Obsolete: " + " ".join(f"#{n}" for n in obsolete))
    if review:
        print("Review:   " + " ".join(f"#{n}" for n in review))
    print(
        "\nVerify before closing anything: a version string can be misparsed,\n"
        "and 'ABSENT' may mean the package is only a transitive dep."
    )


def workflow_actions(ref):
    """Map each action in the workflows at `ref` to the versions it is used at.

    A SHA pin contributes the version from its trailing `# vX.Y.Z` comment, or
    None when there is none -- its hex digits must never be read as a version.
    """
    grep = subprocess.run(
        ["git", "grep", "-h", "-E", r"uses:\s*\S+@", ref, "--", ":/*.yml", ":/*.yaml"],
        capture_output=True,
        text=True,
    ).stdout
    actions = {}
    for line in grep.splitlines():
        m = USES.search(line)
        if not m:
            continue
        name, tag, comment = m.groups()
        version = (comment if comment and version_key(comment) else None) if SHA_PIN.match(tag) else tag
        actions.setdefault(name, set()).add(version)
    return actions


if __name__ == "__main__":
    main()
