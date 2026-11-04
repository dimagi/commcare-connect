"""Prepend a release notes fragment to the Confluence Web Release Notes page.

Run by .github/workflows/release-notes.yml. Exit codes: 0 published (or the release was already on the page),
1 Confluence rejected the update, 3 credentials not configured (the caller reports this as skipped rather than failed).
Not 2 for that, because argparse and the interpreter exit with 2 for their own errors.

Uses urllib rather than httpx, the repo's usual client, so it runs on a bare GitHub runner with nothing installed.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from collections.abc import Mapping
from typing import NamedTuple

# Atlassian service account OAuth clients only work through the API gateway.
BASE_URL = "https://api.atlassian.com/ex/confluence/dbff467f-3c3f-4ced-a2ba-a29e1941edd6"
OAUTH_TOKEN_URL = "https://auth.atlassian.com/oauth/token"
TIMEOUT_SECONDS = 30
EXIT_FAILED = 1
EXIT_NOT_CONFIGURED = 3


class PublishError(Exception):
    pass


class OAuthClient(NamedTuple):
    client_id: str
    client_secret: str


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--page-id", required=True)
    parser.add_argument("--fragment-file", required=True)
    parser.add_argument("--message", required=True)
    parser.add_argument("--tag", required=True, help="skip the update if the page already mentions this release")
    args = parser.parse_args()

    credentials = credentials_from_env(os.environ)
    if credentials is None:
        print("Confluence credentials not configured")
        sys.exit(EXIT_NOT_CONFIGURED)

    with open(args.fragment_file) as f:
        fragment = f.read()
    try:
        authorization = authorization_header(credentials)
        published = publish(BASE_URL, authorization, args.page_id, fragment, args.message, args.tag)
    except PublishError as e:
        print(e)
        sys.exit(EXIT_FAILED)
    if published:
        print(f"Updated Confluence page {args.page_id}")
    else:
        print(f"Confluence page {args.page_id} already has release {args.tag}, left unchanged")


def credentials_from_env(env: Mapping[str, str]) -> OAuthClient | None:
    client_id = env.get("CONFLUENCE_CLIENT_ID")
    client_secret = env.get("CONFLUENCE_CLIENT_SECRET")
    if not (client_id and client_secret):
        return None
    return OAuthClient(client_id, client_secret)


def authorization_header(credentials: OAuthClient) -> str:
    """Exchanges the OAuth client for an access token, which lasts an hour: ample for one run."""
    payload = {
        "grant_type": "client_credentials",
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
    }
    response = _request("POST", OAUTH_TOKEN_URL, None, payload)
    return f"Bearer {response['access_token']}"


def publish(base_url: str, authorization: str, page_id: str, fragment: str, message: str, tag: str) -> bool:
    """Returns False without changing the page when it already mentions the release, so retries are safe.

    The fragment has to mention the release too, or a retry could not recognise it on the page and would add it again.
    """
    if tag not in fragment:
        raise PublishError(f"The release notes do not mention release {tag}, so a retry could add them twice")
    page_url = f"{base_url}/wiki/api/v2/pages/{page_id}"
    page = _request("GET", f"{page_url}?body-format=storage", authorization)
    existing_body = page["body"]["storage"]["value"]
    if tag in existing_body:
        return False
    new_body = prepend_release_notes(existing_body, fragment)
    _request("PUT", page_url, authorization, build_update_payload(page, new_body, message))
    return True


def prepend_release_notes(existing_body: str, fragment: str) -> str:
    if not existing_body.strip():
        return fragment
    return f"{fragment}<hr/>{existing_body}"


def build_update_payload(page: dict, new_body: str, message: str) -> dict:
    return {
        "id": page["id"],
        "status": "current",
        "title": page["title"],
        "body": {"representation": "storage", "value": new_body},
        "version": {"number": page["version"]["number"] + 1, "message": message},
    }


def _request(method: str, url: str, authorization: str | None, payload: dict | None = None) -> dict:
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if authorization:
        headers["Authorization"] = authorization
    request = urllib.request.Request(
        url,
        method=method,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers=headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as e:
        raise PublishError(f"{method} {url} failed with {e.code}: {e.read().decode()}") from e
    except urllib.error.URLError as e:
        raise PublishError(f"{method} {url} failed: {e.reason}") from e


if __name__ == "__main__":
    main()
