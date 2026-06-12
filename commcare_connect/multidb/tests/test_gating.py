from contextlib import contextmanager

from django.test import override_settings

from commcare_connect.multidb import replication


class FakeCursor:
    """Captures executed SQL and returns canned fetch results."""

    def __init__(self, fetchall_result=None):
        self.executed = []
        self._fetchall_result = fetchall_result or []

    def execute(self, sql, params=None):
        self.executed.append((str(sql), params))

    def fetchall(self):
        return self._fetchall_result

    def fetchone(self):
        return self._fetchall_result[0] if self._fetchall_result else None


class FakeConnection:
    def __init__(self, fetchall_result=None):
        self.cursor_obj = FakeCursor(fetchall_result)

    @contextmanager
    def cursor(self):
        yield self.cursor_obj


@override_settings(REPLICATION_ENABLED=True, SECONDARY_DB_ALIAS="secondary")
def test_replication_is_active_when_enabled_and_secondary_configured():
    assert replication.replication_is_active() is True


@override_settings(REPLICATION_ENABLED=False, SECONDARY_DB_ALIAS="secondary")
def test_replication_inactive_when_disabled():
    assert replication.replication_is_active() is False


@override_settings(REPLICATION_ENABLED=True, SECONDARY_DB_ALIAS=None)
def test_replication_inactive_without_secondary():
    assert replication.replication_is_active() is False


def test_publication_exists_true_when_row_returned():
    conn = FakeConnection(fetchall_result=[("tables_for_superset_pub",)])
    assert replication.publication_exists(conn) is True


def test_publication_exists_false_when_no_row():
    conn = FakeConnection(fetchall_result=[])
    assert replication.publication_exists(conn) is False


def test_is_publication_in_sync_true_when_tables_match(monkeypatch):
    desired = {"opportunity_opportunity", "users_user"}
    monkeypatch.setattr(replication, "get_replicated_tables", lambda: desired)
    rows = [(t,) for t in desired]
    conn = FakeConnection(fetchall_result=rows)
    assert replication.is_publication_in_sync(conn) is True


def test_is_publication_in_sync_false_when_table_missing(monkeypatch):
    monkeypatch.setattr(replication, "get_replicated_tables", lambda: {"a", "b"})
    conn = FakeConnection(fetchall_result=[("a",)])  # missing "b"
    assert replication.is_publication_in_sync(conn) is False
