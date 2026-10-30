import base64
import io
import json
import urllib.error

import publish_confluence
import pytest
from publish_confluence import (
    PublishError,
    build_update_payload,
    credentials_from_env,
    prepend_release_notes,
    publish,
)

BASE_URL = "https://confluence.example"
PAGE = {
    "id": "3870556169",
    "status": "current",
    "title": "Web Release Notes",
    "version": {"number": 7},
    "body": {"storage": {"value": "<p>old</p>"}},
}


@pytest.mark.parametrize(
    "env, expected",
    [
        ({"CONFLUENCE_EMAIL": "bot@example.com", "CONFLUENCE_API_TOKEN": "t"}, ("bot@example.com", "t")),
        ({"CONFLUENCE_EMAIL": "bot@example.com", "CONFLUENCE_API_TOKEN": ""}, None),
        ({"CONFLUENCE_API_TOKEN": "t"}, None),
        ({}, None),
    ],
)
def test_credentials_from_env(env, expected):
    assert credentials_from_env(env) == expected


@pytest.mark.parametrize(
    "existing, expected",
    [
        ("<p>old</p>", "<h1>new</h1><hr/><p>old</p>"),
        ("  ", "<h1>new</h1>"),
    ],
)
def test_prepend_release_notes(existing, expected):
    assert prepend_release_notes(existing, "<h1>new</h1>") == expected


def test_build_update_payload_bumps_version():
    assert build_update_payload(PAGE, "B", "M") == {
        "id": "3870556169",
        "status": "current",
        "title": "Web Release Notes",
        "body": {"representation": "storage", "value": "B"},
        "version": {"number": 8, "message": "M"},
    }


class FakeUrlopen:
    def __init__(self, put_error=None):
        self.requests = []
        self.put_error = put_error

    def __call__(self, request, timeout):
        self.requests.append((request, timeout))
        if request.get_method() == "GET":
            return io.BytesIO(json.dumps(PAGE).encode())
        if self.put_error:
            raise self.put_error
        return io.BytesIO(b"{}")


def test_publish_prepends_to_page(monkeypatch):
    fake = FakeUrlopen()
    monkeypatch.setattr(publish_confluence.urllib.request, "urlopen", fake)

    published = publish(BASE_URL, ("bot@example.com", "t"), "3870556169", "<h1>vX</h1>", "Add vX release notes", "vX")

    assert published
    (get, get_timeout), (put, put_timeout) = fake.requests
    assert get.full_url == f"{BASE_URL}/wiki/api/v2/pages/3870556169?body-format=storage"
    assert put.full_url == f"{BASE_URL}/wiki/api/v2/pages/3870556169"
    assert put.get_method() == "PUT"
    assert get_timeout == put_timeout == 30
    expected_auth = "Basic " + base64.b64encode(b"bot@example.com:t").decode()
    assert get.get_header("Authorization") == put.get_header("Authorization") == expected_auth
    body = json.loads(put.data)
    assert body["body"]["value"] == "<h1>vX</h1><hr/><p>old</p>"
    assert body["version"] == {"number": 8, "message": "Add vX release notes"}


def test_publish_raises_on_conflict(monkeypatch):
    conflict = urllib.error.HTTPError(
        f"{BASE_URL}/wiki/api/v2/pages/3870556169", 409, "Conflict", {}, io.BytesIO(b"version mismatch")
    )
    monkeypatch.setattr(publish_confluence.urllib.request, "urlopen", FakeUrlopen(put_error=conflict))

    with pytest.raises(PublishError, match="409.*version mismatch"):
        publish(BASE_URL, ("bot@example.com", "t"), "3870556169", "<h1>vX</h1>", "M", "vX")


def test_publish_skips_release_already_on_page(monkeypatch):
    fake = FakeUrlopen()
    monkeypatch.setattr(publish_confluence.urllib.request, "urlopen", fake)

    published = publish(BASE_URL, ("bot@example.com", "t"), "3870556169", "<h1>old</h1>", "M", "old")

    assert not published
    assert [request.get_method() for request, _ in fake.requests] == ["GET"]


def test_publish_refuses_fragment_without_the_release(monkeypatch):
    fake = FakeUrlopen()
    monkeypatch.setattr(publish_confluence.urllib.request, "urlopen", fake)

    with pytest.raises(PublishError, match="do not mention release vX"):
        publish(BASE_URL, ("bot@example.com", "t"), "3870556169", "<h1>new</h1>", "M", "vX")
    assert fake.requests == []


def test_main_exits_not_configured_without_credentials(monkeypatch, tmp_path):
    fragment = tmp_path / "confluence.html"
    fragment.write_text("<h1>new</h1>")
    monkeypatch.delenv("CONFLUENCE_EMAIL", raising=False)
    monkeypatch.delenv("CONFLUENCE_API_TOKEN", raising=False)
    argv = ["--page-id", "1", "--fragment-file", str(fragment), "--message", "M", "--tag", "vX"]
    monkeypatch.setattr(publish_confluence.sys, "argv", ["publish_confluence.py", *argv])

    with pytest.raises(SystemExit) as exit_info:
        publish_confluence.main()

    # Not 2: argparse and the interpreter use that for their own errors
    assert exit_info.value.code == 3
