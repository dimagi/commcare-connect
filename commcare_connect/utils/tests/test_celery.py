import time
from datetime import timedelta
from unittest import mock

import pytest

from commcare_connect.utils.celery import (
    PENDING_EXPORTS_SESSION_KEY,
    pending_exports,
    render_export_status,
    track_export,
)


@pytest.fixture
def request_with_session(rf):
    request = rf.get("/")
    request.session = {}
    return request


def test_pending_exports_only_returns_the_requested_scope(request_with_session):
    track_export(request_with_session, "opportunity:1", "task-a")
    track_export(request_with_session, "opportunity:2", "task-b")

    assert pending_exports(request_with_session, "opportunity:1") == ["task-a"]


def test_pending_exports_drops_exports_older_than_their_results(request_with_session):
    track_export(request_with_session, "opportunity:1", "task-a")
    request_with_session.session[PENDING_EXPORTS_SESSION_KEY]["task-a"]["started"] = time.time() - 60

    with mock.patch("commcare_connect.utils.celery.current_app") as app:
        app.conf.result_expires = timedelta(seconds=30)
        assert pending_exports(request_with_session, "opportunity:1") == []
    assert request_with_session.session[PENDING_EXPORTS_SESSION_KEY] == {}


@pytest.mark.parametrize(
    "progress, still_pending",
    [
        ({"complete": False, "message": None, "errors": {}}, True),
        ({"complete": True, "message": None, "errors": {}}, False),
        ({"complete": False, "message": None, "errors": {}, "error": "boom"}, False),
    ],
)
def test_export_status_forgets_an_export_once_it_shows_the_outcome(request_with_session, progress, still_pending):
    track_export(request_with_session, "opportunity:1", "task-a")

    with (
        mock.patch("commcare_connect.utils.celery.get_task_progress", return_value=progress),
        mock.patch("commcare_connect.utils.celery.render"),
    ):
        render_export_status(request_with_session, "task-a", download_url="/d", export_status_url="/s")

    assert (pending_exports(request_with_session, "opportunity:1") == ["task-a"]) is still_pending
