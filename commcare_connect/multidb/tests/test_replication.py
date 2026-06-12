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
