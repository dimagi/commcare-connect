#!/usr/bin/env python3
"""Structurally diff a PR's lockfiles against its merge base.

Reading a lockfile diff as text is unreliable -- the diffs run to thousands of
lines and hand-rolled parsers tend to drop packages silently. This parses both
sides of uv.lock and package-lock.json and compares them as data instead.

Usage:
    python scripts/lockdiff.py <pr-number> [--repo dimagi/commcare-connect]

Output: a table of every package whose version moved, marked [direct] when it
is named in pyproject.toml / package.json and [MAJOR] when the leading version
component changed (or, for 0.x versions, the minor). Transitive majors are the
ones worth attention -- they are real upgrades that the Dependabot PR body never
lists.
"""

import argparse
import json
import re

from lockfiles import blob, fetch, parse_npm_lock, parse_uv_lock, remote_for, sh, tomllib


def direct_python(text):
    if not text:
        return set()
    data = tomllib.loads(text)
    proj = data.get("project", {})
    specs = list(proj.get("dependencies", []))
    for group in proj.get("optional-dependencies", {}).values():
        specs.extend(group)
    for group in data.get("dependency-groups", {}).values():
        specs.extend(s for s in group if isinstance(s, str))
    return {re.split(r"[\[<>=!~;\s]", s, maxsplit=1)[0].strip().lower() for s in specs if s}


def direct_npm(text):
    if not text:
        return set()
    data = json.loads(text)
    return set(data.get("dependencies", {})) | set(data.get("devDependencies", {}))


def major(v):
    """The compatibility-defining prefix of a version.

    Under semver (and npm's caret ranges) a 0.x minor bump is breaking, so for
    0.x the minor is part of the "major": 0.3 -> 0.4 counts, 0.3.1 -> 0.3.2 doesn't.
    """
    nums = re.findall(r"\d+", v or "")
    if not nums:
        return None
    if nums[0] == "0" and len(nums) > 1:
        return f"0.{nums[1]}"
    return nums[0]


def majors(versions):
    """The set of major components across every locked version of a package."""
    return {major(v) for v in versions}


def show(versions):
    """Render a version set: a bare version when there is one, else a list."""
    ordered = sorted(versions, key=lambda v: [int(x) for x in re.findall(r"\d+", v)[:3]] or [0])
    return ordered[0] if len(ordered) == 1 else "{" + ", ".join(ordered) + "}"


def report(label, old, new, direct, file_changed=False, present=True):
    moved = {n: (old[n], new[n]) for n in set(old) & set(new) if old[n] != new[n]}
    added = {n: new[n] for n in set(new) - set(old)}
    removed = {n: old[n] for n in set(old) - set(new)}

    print(f"\n{'=' * 72}\n{label}\n{'=' * 72}")
    if not present:
        # "no changes" would imply this stack exists and is untouched.
        print("  not present in this repo")
        return
    if not (moved or added or removed):
        if file_changed:
            # e.g. a requirement-specifier bump that re-resolves to the same
            # versions. Real change, invisible here -- send the reader to the diff.
            print("  lockfile TOUCHED but no resolved version moved")
            print("  -> read `gh pr diff` directly; the change is in the constraints")
        else:
            print("  no changes")
        return

    def is_major(o, n):
        """A major move is any change in the set of major versions locked."""
        return majors(o) != majors(n)

    def tags(name, o=None, n=None):
        t = []
        if name.lower() in direct:
            t.append("direct")
        if o and n and is_major(o, n):
            t.append("MAJOR")
        return f"  [{', '.join(t)}]" if t else ""

    if moved:
        print(f"\n  Version changes ({len(moved)}):")

        # majors and direct deps first -- that is the reading order that matters
        def rank(item):
            name, (o, n) = item
            return (not is_major(o, n), name.lower() not in direct, name.lower())

        for name, (o, n) in sorted(moved.items(), key=rank):
            print(f"    {name}: {show(o)} -> {show(n)}{tags(name, o, n)}")
    if added:
        print(f"\n  Added ({len(added)}):")
        for name, v in sorted(added.items()):
            print(f"    {name}: {show(v)}{tags(name)}")
    if removed:
        print(f"\n  Removed ({len(removed)}):")
        for name, v in sorted(removed.items()):
            print(f"    {name}: {show(v)}{tags(name)}")

    direct_majors = [n for n, (o, n2) in moved.items() if is_major(o, n2) and n.lower() in direct]
    trans_majors = [n for n, (o, n2) in moved.items() if is_major(o, n2) and n.lower() not in direct]
    print(f"\n  Summary: {len(moved)} moved, {len(direct_majors)} direct major, {len(trans_majors)} transitive major")
    if trans_majors:
        print(f"  Transitive majors (not in the PR body): {', '.join(sorted(trans_majors))}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pr", help="PR number")
    ap.add_argument("--repo", default="dimagi/commcare-connect")
    args = ap.parse_args()

    base = sh("gh", "pr", "view", args.pr, "--repo", args.repo, "--json", "baseRefName", "-q", ".baseRefName").strip()

    # The PR head is usually not local; fetch it without touching any branch.
    # Fetch from --repo itself: origin only when it points there, otherwise
    # `pull/<N>/head` would be some other repo's PR with the same number.
    remote = remote_for(args.repo)
    head = fetch(remote, f"pull/{args.pr}/head")
    base_sha = fetch(remote, f"refs/heads/{base}")

    merge_base = sh("git", "merge-base", base_sha, head).strip()
    print(f"PR #{args.pr}  head {head[:12]}  merge-base {merge_base[:12]} ({args.repo} {base})")

    if merge_base == head:
        # <base> already contains this head -- the PR is merged or was
        # pushed from the base. Every diff below would be empty and that would
        # read as "nothing changed" rather than "already landed".
        print(
            f"\n  NOTE: {base} already contains this head -- PR is merged "
            f"or fully included. Diffs below will be empty; check `gh pr view "
            f"{args.pr} --json state,mergedAt` before reviewing further."
        )

    for label, path, parse, direct_path, direct_fn in [
        ("Python (uv.lock)", "uv.lock", parse_uv_lock, "pyproject.toml", direct_python),
        ("JavaScript (package-lock.json)", "package-lock.json", parse_npm_lock, "package.json", direct_npm),
    ]:
        old_text, new_text = blob(merge_base, path), blob(head, path)
        report(
            label,
            parse(old_text),
            parse(new_text),
            direct_fn(blob(head, direct_path)),
            file_changed=old_text != new_text,
            present=bool(old_text or new_text),
        )


if __name__ == "__main__":
    main()
