from io import StringIO

from django.core.management import call_command
from django.test import override_settings

from commcare_connect.multidb import replication
from commcare_connect.multidb.tests.test_gating import FakeConnection


def _executed_sql(conn):
    return " | ".join(sql for sql, _ in conn.cursor_obj.executed)


def test_refresh_publication_sets_tables_and_grants():
    conn = FakeConnection()
    replication.refresh_publication(
        conn, desired_tables={"users_user", "opportunity_opportunity"}, repl_user="postgres_repl"
    )
    sql = _executed_sql(conn)
    assert "ALTER PUBLICATION" in sql
    assert "SET TABLE" in sql
    assert '"opportunity_opportunity"' in sql
    assert '"users_user"' in sql
    assert "GRANT SELECT ON ALL TABLES IN SCHEMA public" in sql
    assert '"postgres_repl"' in sql


def test_refresh_subscription_refreshes_and_grants():
    conn = FakeConnection()
    replication.refresh_subscription(conn, superset_user="superset_readonly")
    sql = _executed_sql(conn)
    assert "ALTER SUBSCRIPTION" in sql
    assert "REFRESH PUBLICATION" in sql
    assert "GRANT SELECT ON ALL TABLES IN SCHEMA public" in sql
    assert '"superset_readonly"' in sql


@override_settings(REPLICATION_ENABLED=False, SECONDARY_DB_ALIAS=None)
def test_command_noops_when_replication_inactive():
    out = StringIO()
    call_command("refresh_replication", stdout=out)
    assert "skipping" in out.getvalue().lower()


@override_settings(REPLICATION_ENABLED=True, SECONDARY_DB_ALIAS="secondary")
def test_command_noops_when_bootstrap_not_run(monkeypatch):
    monkeypatch.setattr(replication, "publication_exists", lambda conn: False)
    out = StringIO()
    call_command("refresh_replication", stdout=out)
    assert "bootstrap" in out.getvalue().lower()


@override_settings(REPLICATION_ENABLED=True, SECONDARY_DB_ALIAS="secondary")
def test_command_noops_when_already_in_sync(monkeypatch):
    monkeypatch.setattr(replication, "publication_exists", lambda conn: True)
    monkeypatch.setattr(replication, "is_publication_in_sync", lambda conn: True)
    out = StringIO()
    call_command("refresh_replication", stdout=out)
    assert "in sync" in out.getvalue().lower()
