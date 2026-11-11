"""Write the Slack message for a failed staging rebuild.

Names the staged branches behind the failure and mentions whoever started each one, looked up in Slack by
commit email with SLACK_BOT_TOKEN (needs the `users:read.email` scope). Run by
.github/workflows/rebuild-staging.yml when the rebuild job fails.
"""

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import httpx

from deploy import staging_add as staging

APPS_DIR = "commcare_connect"
TRUNK_REF = f"origin/{staging.TRUNK}"
SLACK_LOOKUP_URL = "https://slack.com/api/users.lookupByEmail"
MAX_DETAIL_LINES = 20
# Slack rejects a text block over 3000 characters
MAX_DETAIL_CHARS = 2800

CONFLICTS_WITH = re.compile(r"(\S+) conflicts with (\S+)")
LEAF_NODES = re.compile(r"multiple leaf nodes in the migration graph: \((.+)\)")
NEEDS_MIGRATIONS = re.compile(r"^Migrations for '(\w+)':", flags=re.MULTILINE)
CHECK_ERROR = re.compile(r"\([\w.]+\.E\d+\)")
CHECK_ERROR_APP = re.compile(r"^(\w+)\.\S*: \([\w.]+\.E\d+\)", flags=re.MULTILINE)
OWNER_COMMENT = re.compile(r"^\s*-\s*(\S+)\s*#\s*(\S+)", flags=re.MULTILINE)

HEADLINES = {
    "build": "*Staging rebuild failed:* the branches don't merge.",
    "check": "*Staging rebuild failed:* `manage.py check` found errors.",
    "migrations": "*Staging rebuild failed:* the migration check failed.",
    "other": "*Staging rebuild failed.*",
}


@dataclass
class Suspect:
    branch: str
    owner: str
    slack_id: str

    def mention(self):
        if self.slack_id:
            return f"<@{self.slack_id}>"
        return slack_escape(self.owner) or "owner unknown"


def main():
    args = parse_args()
    repo = Path.cwd()
    output = staging.ANSI_ESCAPE.sub("", Path(args.log).read_text()) if args.log else ""
    config_text = Path(args.config).read_text()
    branches = [b for b in staging.StagingConfig.parse(config_text).branches if b != staging.TRUNK]

    names = suspect_branches(repo=repo, step=args.step, output=output, branches=branches)
    suspects = describe_suspects(
        repo=repo, names=names, config_text=config_text, slack_token=os.environ.get("SLACK_BOT_TOKEN", "")
    )
    payload = slack_payload(
        step=args.step, details=failure_details(step=args.step, output=output), suspects=suspects, run_url=args.run_url
    )
    Path(args.payload_file).write_text(json.dumps(payload))


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--step", choices=HEADLINES, required=True, help="The step that failed")
    parser.add_argument("--log", help="That step's output")
    parser.add_argument("--config", required=True, help="The staging branch list (YAML)")
    parser.add_argument("--run-url", required=True)
    parser.add_argument("--payload-file", required=True, help="Where to write the Slack payload")
    return parser.parse_args()


# Which branches caused the failure


def suspect_branches(*, repo, step, output, branches):
    """The listed branches behind a failure, in list order."""
    if step == "other":
        return []
    if step == "build":
        found = build_failures(output)
    elif step == "migrations" and (paths := leaf_migration_paths(output)):
        found = branches_adding(repo=repo, branches=branches, paths=paths)
    elif step == "migrations":
        found = branches_changing(repo=repo, branches=branches, apps=NEEDS_MIGRATIONS.findall(output))
    else:
        found = branches_changing(repo=repo, branches=branches, apps=CHECK_ERROR_APP.findall(output))
    return [branch for branch in branches if branch in found]


def build_failures(output):
    """Branches that failed to merge, weren't found, or were named in a conflict."""
    conflicting = {staging.normalize_entry(name) for pair in CONFLICTS_WITH.findall(output) for name in pair}
    return staging.parse_failures(output) | conflicting


def leaf_migration_paths(output):
    """Migration files from Django's "multiple leaf nodes" error, e.g. `(0151_a, 0151_b in opportunity)`."""
    match = LEAF_NODES.search(output)
    if not match:
        return []
    paths = []
    for group in match.group(1).split("; "):
        names, _, app = group.rpartition(" in ")
        paths += [f"{APPS_DIR}/{app}/migrations/{name.strip()}.py" for name in names.split(",")]
    return paths


def branches_adding(*, repo, branches, paths):
    """Branches that have at least one of `paths` when `main` doesn't."""
    new_paths = [path for path in paths if not file_exists(repo=repo, ref=TRUNK_REF, path=path)]
    return {
        branch
        for branch in branches
        if (ref := existing_ref(repo=repo, branch=branch))
        and any(file_exists(repo=repo, ref=ref, path=path) for path in new_paths)
    }


def branches_changing(*, repo, branches, apps):
    """Branches that change files in any of the given Django apps."""
    if not apps:
        return set()
    dirs = [f"{APPS_DIR}/{app}/" for app in apps]
    return {
        branch
        for branch in branches
        if (ref := existing_ref(repo=repo, branch=branch))
        and staging.git(repo, "diff", "--name-only", f"{TRUNK_REF}...{ref}", "--", *dirs)
    }


# Who to tell


def describe_suspects(*, repo, names, config_text, slack_token):
    owners = dict(OWNER_COMMENT.findall(config_text))
    return [
        Suspect(
            branch=name,
            owner=owners.get(name, ""),
            slack_id=slack_user_id(email=author_email(repo=repo, branch=name), token=slack_token),
        )
        for name in names
    ]


def author_email(*, repo, branch):
    """Who started the branch: the author of its first commit that isn't on `main`, ignoring merges."""
    ref = existing_ref(repo=repo, branch=branch)
    if not ref:
        return ""
    # `-1` would apply before `--reverse`, so take the last line of the newest-first list instead
    emails = staging.git(repo, "log", "--no-merges", "--format=%ae", f"{TRUNK_REF}..{ref}").splitlines()
    return emails[-1] if emails else ""


def slack_user_id(*, email, token):
    """The Slack user with this email, or "" if there's no match or the lookup fails."""
    if not (email and token):
        return ""
    try:
        response = httpx.get(
            SLACK_LOOKUP_URL, params={"email": email}, headers={"Authorization": f"Bearer {token}"}, timeout=10
        )
        data = response.json()
    except (httpx.HTTPError, ValueError) as error:
        print(f"Slack lookup for {email} failed: {error}", file=sys.stderr)
        return ""
    if not data.get("ok"):
        # `missing_scope` means the bot token needs `users:read.email`
        print(f"Slack lookup for {email} failed: {data.get('error')}", file=sys.stderr)
        return ""
    return data["user"]["id"]


# The message


def failure_details(*, step, output):
    """The lines of the failed step's output that explain the failure."""
    if step == "other":
        return ""
    if step == "build":
        lines = list(dict.fromkeys(staging.failure_details(output).splitlines()))
    elif step == "check":
        lines = [line for line in output.splitlines() if CHECK_ERROR.search(line)]
    else:
        lines = lines_from_first(output=output, markers=("CommandError", "Migrations for"))
    return "\n".join(lines[:MAX_DETAIL_LINES])[:MAX_DETAIL_CHARS]


def lines_from_first(*, output, markers):
    lines = output.splitlines()
    for index, line in enumerate(lines):
        if any(marker in line for marker in markers):
            return lines[index:]
    return []


def slack_payload(*, step, details, suspects, run_url):
    config_url = f"https://github.com/{staging.CONFIG_REPO}/blob/main/{staging.CONFIG_FILE}"
    blocks = [
        section(f"{HEADLINES[step]} Staging keeps its current version until the branch list builds."),
        section(f"```{code_block_text(details) or 'See the run log'}```"),
    ]
    if suspects:
        lines = "\n".join(f"• `{slack_escape(suspect.branch)}`: {suspect.mention()}" for suspect in suspects)
        blocks.append(section(f"*Likely cause:*\n{lines}"))
    links = f"<{run_url}|View run> · <{config_url}|Edit branch list>"
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": links}]})
    return {"text": "Staging rebuild failed", "blocks": blocks}


def section(text):
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


def slack_escape(text):
    """Make text from logs, branch names or the branch list show literally, so it can't add mentions or links."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def code_block_text(text):
    """Escaped text for a ``` block, with any ``` in it changed so it can't end the block early."""
    escaped = slack_escape(text).replace("```", "ˋˋˋ")
    # Escaping lengthens the text, so trim again to stay under Slack's block limit
    return escaped[:MAX_DETAIL_CHARS]


# Git


def existing_ref(*, repo, branch):
    """The remote-tracking ref for a list entry, or "" if it doesn't exist (e.g. the branch was deleted)."""
    owner, _, name = branch.rpartition(":")
    ref = f"refs/remotes/{owner or 'origin'}/{name}"
    return ref if staging.ref_exists(repo=repo, ref=ref) else ""


def file_exists(*, repo, ref, path):
    result = subprocess.run(["git", "cat-file", "-e", f"{ref}:{path}"], cwd=repo, capture_output=True)
    return result.returncode == 0


if __name__ == "__main__":
    main()
