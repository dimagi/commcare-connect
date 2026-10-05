#!/usr/bin/env python3
"""Structurally diff a PR's lockfiles against its merge base.

Reading a lockfile diff as text is unreliable -- the diffs run to thousands of
lines and hand-rolled parsers tend to drop packages silently. This parses both
sides of uv.lock and package-lock.json and compares them as data instead.

Usage:
    python scripts/lockdiff.py <pr-number> [--repo dimagi/commcare-connect]

Output: a table of every package whose version moved, marked [direct] when it
is named in pyproject.toml / package.json and [MAJOR] when the leading version
component changed. Transitive majors are the ones worth attention -- they are
real upgrades that the Dependabot PR body never lists.
"""

import argparse
import json
import os
import re
import subprocess
import sys

try:
    import tomllib
except ImportError:  # Python < 3.11
    try:
        import tomli as tomllib
    except ImportError:
        # Re-exec under any 3.11+ interpreter we can find. The repo being
        # reviewed is not necessarily the one this skill lives in, so don't
        # assume a local .venv exists.
        import os
        import shutil

        if os.environ.get("_TOML_REEXEC"):
            sys.exit("needs Python 3.11+ (for tomllib) or `pip install tomli`")
        root = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True).stdout.strip()
        candidates = [os.path.join(root, ".venv/bin/python")] if root else []
        candidates += [shutil.which(n) for n in ("python3.13", "python3.12", "python3.11", "python3")]
        for exe in candidates:
            if not exe or not os.path.exists(exe):
                continue
            probe = subprocess.run([exe, "-c", "import tomllib"], capture_output=True)
            if probe.returncode == 0:
                os.environ["_TOML_REEXEC"] = "1"
                os.execv(exe, [exe, os.path.abspath(__file__), *sys.argv[1:]])
        sys.exit("needs Python 3.11+ (for tomllib) or `pip install tomli`")


def sh(*cmd, check=True):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if check and r.returncode != 0:
        sys.exit(f"command failed: {' '.join(cmd)}\n{r.stderr.strip()}")
    return r.stdout


def blob(ref, path):
    """Return a file's contents at a git ref, or None if absent there."""
    r = subprocess.run(["git", "show", f"{ref}:{path}"], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else None


def parse_uv_lock(text):
    if not text:
        return {}
    data = tomllib.loads(text)
    return {p["name"]: p["version"] for p in data.get("package", []) if "version" in p}


def parse_npm_lock(text):
    if not text:
        return {}
    data = json.loads(text)
    out = {}
    for path, meta in data.get("packages", {}).items():
        if not path or "version" not in meta:
            continue  # "" is the root project itself
        # "node_modules/a/node_modules/b" -> "b"
        out[path.split("node_modules/")[-1]] = meta["version"]
    return out


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
    return {re.split(r"[\[<>=!~;\s]", s, 1)[0].strip().lower() for s in specs if s}


def direct_npm(text):
    if not text:
        return set()
    data = json.loads(text)
    return set(data.get("dependencies", {})) | set(data.get("devDependencies", {}))


def major(v):
    m = re.match(r"\D*(\d+)", v or "")
    return m.group(1) if m else None


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

    def tags(name, o=None, n=None):
        t = []
        if name.lower() in direct:
            t.append("direct")
        if o and n and major(o) != major(n):
            t.append("MAJOR")
        return f"  [{', '.join(t)}]" if t else ""

    if moved:
        print(f"\n  Version changes ({len(moved)}):")

        # majors and direct deps first -- that is the reading order that matters
        def rank(item):
            name, (o, n) = item
            return (major(o) == major(n), name.lower() not in direct, name.lower())

        for name, (o, n) in sorted(moved.items(), key=rank):
            print(f"    {name}: {o} -> {n}{tags(name, o, n)}")
    if added:
        print(f"\n  Added ({len(added)}):")
        for name, v in sorted(added.items()):
            print(f"    {name}: {v}{tags(name)}")
    if removed:
        print(f"\n  Removed ({len(removed)}):")
        for name, v in sorted(removed.items()):
            print(f"    {name}: {v}{tags(name)}")

    direct_majors = [n for n, (o, n2) in moved.items() if major(o) != major(n2) and n.lower() in direct]
    trans_majors = [n for n, (o, n2) in moved.items() if major(o) != major(n2) and n.lower() not in direct]
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
    sh("git", "fetch", "--quiet", f"https://github.com/{args.repo}.git", f"pull/{args.pr}/head")
    head = sh("git", "rev-parse", "FETCH_HEAD").strip()
    sh("git", "fetch", "--quiet", "origin", base, check=False)

    merge_base = sh("git", "merge-base", f"origin/{base}", head).strip()
    print(f"PR #{args.pr}  head {head[:12]}  merge-base {merge_base[:12]} (origin/{base})")

    if merge_base == head:
        # origin/<base> already contains this head -- the PR is merged or was
        # pushed from the base. Every diff below would be empty and that would
        # read as "nothing changed" rather than "already landed".
        print(
            f"\n  NOTE: origin/{base} already contains this head -- PR is merged "
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
