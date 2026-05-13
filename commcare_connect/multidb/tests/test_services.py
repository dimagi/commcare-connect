from unittest import mock

import pytest

from commcare_connect.multidb import services
from commcare_connect.multidb.models import SupersetReplicatedTable
from commcare_connect.multidb.services import (
    ReplicationSyncError,
    alter_publication_on_primary,
    get_model_label_choices,
    get_replicated_table_names,
    refresh_subscription_on_secondary,
    sync_replicated_tables,
)


class TestGetModelLabelChoices:
    def test_returns_only_local_app_models(self, settings):
        settings.LOCAL_APPS = ["commcare_connect.opportunity"]
        choices = get_model_label_choices()
        app_labels = {label.split(".")[0] for label, _ in choices}
        assert app_labels == {"opportunity"}

    def test_choice_format_includes_db_table(self, settings):
        settings.LOCAL_APPS = ["commcare_connect.opportunity"]
        choices = dict(get_model_label_choices())
        display = choices["opportunity.opportunity"]
        assert "opportunity.Opportunity" in display
        assert "opportunity_opportunity" in display


@pytest.fixture
def empty_table(db):
    SupersetReplicatedTable.objects.all().delete()


@pytest.mark.django_db
@pytest.mark.usefixtures("empty_table")
class TestGetReplicatedTableNames:
    def test_resolves_to_db_table_names(self):
        SupersetReplicatedTable.objects.create(model_label="opportunity.opportunity")
        SupersetReplicatedTable.objects.create(model_label="users.user")
        names = get_replicated_table_names()
        assert "opportunity_opportunity" in names
        assert "users_user" in names

    def test_skips_invalid_labels(self):
        SupersetReplicatedTable.objects.create(model_label="opportunity.opportunity")
        SupersetReplicatedTable.objects.create(model_label="fake_app.fake_model")
        names = get_replicated_table_names()
        assert names == ["opportunity_opportunity"]

    def test_returns_sorted(self):
        SupersetReplicatedTable.objects.create(model_label="users.user")
        SupersetReplicatedTable.objects.create(model_label="opportunity.opportunity")
        assert get_replicated_table_names() == sorted(get_replicated_table_names())


class TestAlterPublicationOnPrimary:
    def test_empty_table_list_raises(self):
        with pytest.raises(ReplicationSyncError, match="empty table list"):
            alter_publication_on_primary([])

    @mock.patch("commcare_connect.multidb.services.connections")
    def test_raises_when_publication_missing(self, mock_connections):
        mock_cursor = mock.MagicMock()
        mock_cursor.fetchone.return_value = None
        mock_connections.__getitem__.return_value.cursor.return_value.__enter__.return_value = mock_cursor

        with pytest.raises(ReplicationSyncError, match="does not exist on the primary"):
            alter_publication_on_primary(["foo"])

    @mock.patch("commcare_connect.multidb.services.connections")
    def test_alters_when_publication_exists(self, mock_connections):
        mock_cursor = mock.MagicMock()
        mock_cursor.fetchone.return_value = ("tables_for_superset_pub",)
        mock_connections.__getitem__.return_value.cursor.return_value.__enter__.return_value = mock_cursor

        alter_publication_on_primary(["opportunity_opportunity", "users_user"])

        executed_sql = [call.args[0] for call in mock_cursor.execute.call_args_list]
        assert any("ALTER PUBLICATION" in sql and "opportunity_opportunity" in sql for sql in executed_sql)


class TestRefreshSubscriptionOnSecondary:
    def test_raises_when_secondary_not_configured(self, settings):
        settings.SECONDARY_DB_ALIAS = None
        with pytest.raises(ReplicationSyncError, match="Secondary database is not configured"):
            refresh_subscription_on_secondary()

    def test_raises_when_creds_missing(self, settings):
        settings.SECONDARY_DB_ALIAS = "secondary"
        settings.SECONDARY_DB_SUPERUSER_NAME = None
        settings.SECONDARY_DB_SUPERUSER_PASSWORD = None
        with pytest.raises(ReplicationSyncError, match="SECONDARY_DB_SUPERUSER_NAME"):
            refresh_subscription_on_secondary()


@pytest.mark.django_db
@pytest.mark.usefixtures("empty_table")
class TestSyncReplicatedTables:
    def test_records_success_timestamps(self):
        SupersetReplicatedTable.objects.create(model_label="opportunity.opportunity")

        with mock.patch.object(services, "alter_publication_on_primary"), mock.patch.object(
            services, "refresh_subscription_on_secondary"
        ):
            result = sync_replicated_tables()

        assert result.ok
        entry = SupersetReplicatedTable.objects.get(model_label="opportunity.opportunity")
        assert entry.config_synced_at is not None
        assert entry.config_sync_error == ""

    def test_records_errors_on_primary_failure(self):
        SupersetReplicatedTable.objects.create(model_label="opportunity.opportunity")

        with mock.patch.object(
            services, "alter_publication_on_primary", side_effect=ReplicationSyncError("primary boom")
        ), mock.patch.object(services, "refresh_subscription_on_secondary"):
            result = sync_replicated_tables()

        assert not result.ok
        assert not result.primary_synced
        assert any("primary boom" in e for e in result.errors)
        entry = SupersetReplicatedTable.objects.get(model_label="opportunity.opportunity")
        assert "primary boom" in entry.config_sync_error

    def test_records_errors_on_secondary_failure(self):
        SupersetReplicatedTable.objects.create(model_label="opportunity.opportunity")

        with mock.patch.object(services, "alter_publication_on_primary"), mock.patch.object(
            services, "refresh_subscription_on_secondary", side_effect=ReplicationSyncError("secondary boom")
        ):
            result = sync_replicated_tables()

        assert not result.ok
        assert result.primary_synced
        assert not result.secondary_synced
        entry = SupersetReplicatedTable.objects.get(model_label="opportunity.opportunity")
        assert "secondary boom" in entry.config_sync_error
