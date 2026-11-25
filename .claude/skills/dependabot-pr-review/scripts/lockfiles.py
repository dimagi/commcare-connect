"""Helpers shared by lockdiff.py and triage.py.

Importing this module also guarantees `tomllib` is available: on Python < 3.11
without `tomli` it re-execs the calling script under a 3.11+ interpreter.
"""

import json
import os
import re
import shutil
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
        # assume a local .venv exists. Re-exec the *calling* script
        # (sys.argv[0]), not this module.
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
                os.execv(exe, [exe, os.path.abspath(sys.argv[0]), *sys.argv[1:]])
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


# --- Reaching the right repository -------------------------------------------
#
# Both scripts take --repo but read history from the local checkout. Fetching
# from `origin` when origin is some other repo (or a fork) silently reads the
# wrong PR / the wrong main, so only use origin when it really is --repo.


def remote_for(repo):
    """`origin` if it points at `repo` (owner/name), else the public HTTPS URL.

    On a CI runner origin carries the auth header actions/checkout installed;
    the HTTPS URL is what works locally when origin is an SSH remote and no key
    is loaded.
    """
    url = subprocess.run(["git", "remote", "get-url", "origin"], capture_output=True, text=True).stdout.strip()
    m = re.search(r"github\.com[:/]+([^/]+/[^/]+?)(?:\.git)?/?$", url)
    if m and m.group(1).lower() == repo.lower():
        return "origin"
    return f"https://github.com/{repo}.git"


def fetch(remote, ref):
    """Fetch `ref` from `remote` without touching any branch; return its SHA."""
    r = subprocess.run(["git", "fetch", "--quiet", remote, ref], capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"could not fetch {ref} from {remote}\n{r.stderr.strip()}")
    return sh("git", "rev-parse", "FETCH_HEAD").strip()


# --- Lockfile parsing ---------------------------------------------------------
#
# Both lockfiles can hold several versions of one package: npm nests a private
# copy under "node_modules/a/node_modules/b", and a uv resolution fork can lock
# two versions behind different markers. Mapping name -> version would let the
# last one encountered win, which both hides real changes and invents fake ones
# as dict ordering shifts. Map to a set of versions instead.


def parse_uv_lock(text):
    if not text:
        return {}
    out = {}
    for pkg in tomllib.loads(text).get("package", []):
        if "version" in pkg:
            out.setdefault(pkg["name"].lower(), set()).add(pkg["version"])
    return out


def parse_npm_lock(text, top_level_only=False):
    """Map package name -> set of locked versions.

    With top_level_only, keep only the hoisted "node_modules/<name>" copies --
    the ones package.json declares and Dependabot bumps -- and ignore private
    copies nested under another package.
    """
    if not text:
        return {}
    out = {}
    for path, meta in json.loads(text).get("packages", {}).items():
        if not path or "version" not in meta:
            continue  # "" is the root project itself
        if top_level_only and path.count("node_modules/") != 1:
            continue
        # "node_modules/a/node_modules/b" -> "b"
        out.setdefault(path.split("node_modules/")[-1], set()).add(meta["version"])
    return out


def version_key(v):
    """Comparable tuple; tolerates 'v3.0.1', '25.1', '4'."""
    return tuple(int(x) for x in re.findall(r"\d+", v or "")[:3])
