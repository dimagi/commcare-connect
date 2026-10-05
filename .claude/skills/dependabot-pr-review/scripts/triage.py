#!/usr/bin/env python3
"""Triage every open Dependabot PR against current origin/main.

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


def show(path):
    r = subprocess.run(["git", "show", f"origin/main:{path}"], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ""


def ver(v):
    """Comparable tuple; tolerates 'v3.0.1', '25.1', '4'."""
    return tuple(int(x) for x in re.findall(r"\d+", v or "")[:3])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default="dimagi/commcare-connect")
    args = ap.parse_args()

    subprocess.run(["git", "fetch", "-q", "origin", "main"], check=False)

    python_pkgs = {p["name"].lower(): p.get("version") for p in tomllib.loads(show("uv.lock")).get("package", [])}

    npm_pkgs = {}
    for path, meta in json.loads(show("package-lock.json") or "{}").get("packages", {}).items():
        if path and "version" in meta:
            npm_pkgs[path.split("node_modules/")[-1]] = meta["version"]

    # An action's "manifest" is every `uses:` line in the workflows.
    actions = {}
    grep = subprocess.run(
        ["git", "grep", "-h", "-oE", r"uses: [^ ]+", "origin/main", "--", "*.yml", "*.yaml"],
        capture_output=True,
        text=True,
    ).stdout
    for line in grep.splitlines():
        spec = line.split("uses: ", 1)[-1].strip()
        if "@" in spec:
            name, tag = spec.rsplit("@", 1)
            actions.setdefault(name, set()).add(tag)

    prs = json.loads(
        subprocess.run(
            [
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
            ],
            capture_output=True,
            text=True,
        ).stdout
        or "[]"
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
        kind = (
            "py" if pkg.lower() in python_pkgs else "npm" if pkg in npm_pkgs else "action" if pkg in actions else None
        )
        if kind is None:
            rows.append(
                (n, created, pkg[:40], f"{old}->{new}", "ABSENT", "OBSOLETE - not on main; confirm deliberate removal")
            )
            continue

        cur = python_pkgs.get(pkg.lower()) or npm_pkgs.get(pkg) or sorted(actions.get(pkg, set()), key=ver)[-1]
        if ver(cur) >= ver(new):
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


if __name__ == "__main__":
    main()
