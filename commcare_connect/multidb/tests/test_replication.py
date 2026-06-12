from commcare_connect.multidb import replication
from commcare_connect.multidb.constants import REPLICATION_EXCLUDED_MODELS, REPLICATION_INCLUDED_MODELS


def test_get_replicated_models_returns_included_list():
    assert replication.get_replicated_models() == REPLICATION_INCLUDED_MODELS


def test_get_replicated_tables_returns_db_table_names():
    tables = replication.get_replicated_tables()
    assert "opportunity_opportunity" in tables
    assert tables == {m._meta.db_table for m in REPLICATION_INCLUDED_MODELS}


def test_included_and_excluded_are_disjoint():
    overlap = set(REPLICATION_INCLUDED_MODELS) & set(REPLICATION_EXCLUDED_MODELS)
    assert overlap == set(), f"models in both lists: {overlap}"


def test_no_unclassified_local_models():
    # Every local model is in exactly one list; this set must be empty.
    assert replication.get_unclassified_models() == set()


def test_unclassified_excludes_pghistory_events_and_m2m():
    # The "must classify" set never includes pghistory Event models or
    # auto-created through tables.
    import pghistory.models

    must_classify = replication.get_models_requiring_classification()
    for model in must_classify:
        assert not model._meta.auto_created
        assert not issubclass(model, pghistory.models.Event)


def test_table_count_rows_uses_replicated_models():
    from commcare_connect.multidb.management.commands import logical_replication_status

    class C:
        def __init__(self, n):
            self.n = n
            self.executed = []

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, params=None):
            self.executed.append(sql)

        def fetchone(self):
            return (self.n,)

    class Conn:
        def __init__(self, n):
            self.n = n

        def cursor(self):
            return C(self.n)

    rows = logical_replication_status.table_count_rows(Conn(5), Conn(3))
    tables = {r[0] for r in rows}
    assert "opportunity_opportunity" in tables
    assert all(r[1] == 5 and r[2] == 3 for r in rows)
