from commcare_connect.multidb.management.commands import setup_logical_replication
from commcare_connect.multidb.tests.test_gating import FakeCursor


def test_transfer_subscription_ownership_issues_alter_owner():
    cursor = FakeCursor()
    setup_logical_replication.transfer_subscription_ownership(cursor, "app_secondary_role")
    sql = " ".join(s for s, _ in cursor.executed)
    assert "ALTER SUBSCRIPTION" in sql
    assert "OWNER TO" in sql
    assert '"app_secondary_role"' in sql
