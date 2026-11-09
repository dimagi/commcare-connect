from types import SimpleNamespace
from unittest import mock

import pytest
from django.test import RequestFactory
from django.urls import reverse
from redis.exceptions import ConnectionError as RedisConnectionError

from commcare_connect.health import checks as health_checks
from commcare_connect.health.checks import RedisClient, get_check_subsets
from commcare_connect.health.views import has_valid_token
from commcare_connect.utils.sentry import make_traces_sampler
from config.celery_app import app as celery_app


@pytest.fixture
def redis_up():
    with mock.patch.object(RedisClient, "ping", new_callable=mock.AsyncMock, return_value=True) as ping:
        yield ping


@pytest.fixture
def redis_down():
    with mock.patch.object(RedisClient, "ping", new_callable=mock.AsyncMock, side_effect=RedisConnectionError()):
        yield


@pytest.fixture
def celery_workers():
    """A worker that answers the ping and consumes the default queue, unless a test changes it."""
    state = {
        "ping": [{"celery@worker": {"ok": "pong"}}],
        "active_queues": {"celery@worker": [{"name": "celery"}]},
    }
    with (
        mock.patch.object(celery_app.control, "ping", side_effect=lambda **kw: state["ping"]),
        mock.patch.object(celery_app.control, "inspect") as inspect,
    ):
        inspect.return_value.active_queues.side_effect = lambda: state["active_queues"]
        yield state


class TestUp:
    @pytest.mark.parametrize("path", ["/up", "/up/", "/export/up", "/export/up/"])
    def test_ok_without_database_or_auth(self, client, settings, django_assert_num_queries, path):
        settings.HEALTH_CHECK_TOKENS = ["secret"]
        # Zero queries also rules out the savepoint ATOMIC_REQUESTS would open.
        with django_assert_num_queries(0):
            response = client.get(path)
        assert response.status_code == 200
        assert response.content == b"OK"
        assert "no-cache" in response["Cache-Control"]

    @pytest.mark.parametrize("name, path", [("up", "/up"), ("export_up", "/export/up")])
    def test_reverse(self, name, path):
        assert reverse(name) == path


@pytest.mark.django_db(transaction=True)
class TestStatusGeneral:
    def test_healthy(self, client, redis_up):
        response = client.get("/status/", HTTP_ACCEPT="application/json")
        assert response.status_code == 200
        body = response.json()
        assert set(body.values()) == {"OK"}
        assert any(key.startswith("Database") for key in body)
        assert any(key.startswith("Cache") for key in body)
        assert any(key.startswith("Redis") for key in body)
        redis_up.assert_awaited_once()

    def test_redis_down(self, client, redis_down):
        response = client.get("/status/", HTTP_ACCEPT="application/json")
        assert response.status_code == 500
        redis_result = next(value for key, value in response.json().items() if key.startswith("Redis"))
        assert "Unavailable" in redis_result

    def test_does_not_check_celery(self, client, redis_up):
        with mock.patch.object(celery_app.control, "ping") as ping:
            assert client.get("/status/").status_code == 200
        ping.assert_not_called()

    def test_plain_text(self, client, redis_up):
        response = client.get("/status/?format=text")
        assert response.status_code == 200
        assert response["Content-Type"].startswith("text/plain")


class TestStatusCelery:
    def test_healthy(self, client, celery_workers):
        response = client.get("/status/celery/", HTTP_ACCEPT="application/json")
        assert response.status_code == 200
        assert response.json() == {"CeleryPing()": "OK"}

    def test_no_workers(self, client, celery_workers):
        celery_workers["ping"] = []
        response = client.get("/status/celery/", HTTP_ACCEPT="application/json")
        assert response.status_code == 500
        assert list(response.json().values()) == ["Unavailable: Celery workers unavailable"]

    def test_queue_without_worker(self, client, celery_workers):
        celery_workers["active_queues"] = {"celery@worker": [{"name": "other"}]}
        response = client.get("/status/celery/", HTTP_ACCEPT="application/json")
        assert response.status_code == 500
        assert list(response.json().values()) == ["Unavailable: No worker for Celery task queue celery"]

    def test_broker_unreachable(self, client):
        with mock.patch.object(celery_app.control, "ping", side_effect=OSError):
            response = client.get("/status/celery/", HTTP_ACCEPT="application/json")
        assert response.status_code == 500
        assert list(response.json().values()) == ["Unavailable: IOError"]


class TestStatusAccess:
    def test_unknown_subset_404(self, client):
        assert client.get("/status/nope/").status_code == 404

    def test_bad_token_404(self, client, settings):
        settings.HEALTH_CHECK_TOKENS = ["secret"]
        assert client.get("/status/celery/?token=wrong").status_code == 404

    def test_valid_token(self, client, settings, celery_workers):
        settings.HEALTH_CHECK_TOKENS = ["secret"]
        assert client.get("/status/celery/", HTTP_AUTHORIZATION="Bearer secret").status_code == 200

    def test_not_cached(self, client, celery_workers):
        assert "no-cache" in client.get("/status/celery/")["Cache-Control"]


class TestHasValidToken:
    TOKENS = ["secret", "other"]

    @pytest.mark.parametrize(
        "query, header, expected",
        [
            ("", "", False),
            ("?token=wrong", "", False),
            ("?token=", "", False),
            ("?token=secret", "", True),
            ("?token=other", "", True),
            ("", "Bearer secret", True),
            ("", "bearer other", True),
            ("", "Bearer  secret ", True),
            ("", "Bearer wrong", False),
            ("", "Bearer ", False),
            ("", "Basic secret", False),
            ("", "secret", False),
            ("", "Token secret", False),
            ("?token=wrong", "Bearer secret", True),
        ],
    )
    def test_with_tokens(self, settings, query, header, expected):
        settings.HEALTH_CHECK_TOKENS = self.TOKENS
        request = RequestFactory().get(f"/status/{query}", HTTP_AUTHORIZATION=header)
        assert has_valid_token(request) is expected

    def test_open_when_no_tokens(self, settings):
        settings.HEALTH_CHECK_TOKENS = []
        assert has_valid_token(RequestFactory().get("/status/")) is True


@pytest.mark.parametrize(
    "aliases, expected",
    [
        (["default"], [{"alias": "default"}]),
        (["default", "secondary"], [{"alias": "default"}, {"alias": "secondary"}]),
    ],
)
def test_general_checks_every_database(monkeypatch, aliases, expected):
    # Patched on the module rather than overridden: Django warns that overriding DATABASES is unsafe.
    monkeypatch.setattr(health_checks, "settings", SimpleNamespace(DATABASES={alias: {} for alias in aliases}))
    database_checks = [kwargs for check, kwargs in _with_kwargs(get_check_subsets()["general"]) if "Database" in check]
    assert database_checks == expected


def _with_kwargs(checks):
    return [check if isinstance(check, tuple) else (check, {}) for check in checks]


@pytest.mark.parametrize(
    "path, expected",
    [
        ("/up", 0),
        ("/up/", 0),
        ("/export/up", 0),
        ("/status/", 0),
        ("/status/celery/", 0),
        ("/export/opportunity/1/", 0.5),
        ("/upload", 0.5),
    ],
)
def test_traces_sampler_skips_health_checks(path, expected):
    sampler = make_traces_sampler(0.5)
    assert sampler({"wsgi_environ": {"PATH_INFO": path}}) == expected


def test_traces_sampler_follows_parent():
    sampler = make_traces_sampler(0.5)
    assert sampler({"parent_sampled": True}) == 1.0
    assert sampler({"parent_sampled": False}) == 0.0
    assert sampler({}) == 0.5
