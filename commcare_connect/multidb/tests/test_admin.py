from unittest import mock

import pytest
from django.contrib import messages
from django.contrib.admin.sites import AdminSite

from commcare_connect.multidb.admin import SupersetReplicatedTableAdmin
from commcare_connect.multidb.models import SupersetReplicatedTable
from commcare_connect.multidb.services import SyncResult


def _ok_result():
    return SyncResult(primary_synced=True, secondary_synced=True, errors=[])


def _failed_result(errors):
    return SyncResult(primary_synced=False, secondary_synced=False, errors=errors)


def _make_request(rf):
    request = rf.post("/admin/")
    setattr(request, "session", {})
    setattr(request, "_messages", messages.storage.default_storage(request))
    return request


@pytest.fixture
def admin_instance():
    return SupersetReplicatedTableAdmin(SupersetReplicatedTable, AdminSite())


@pytest.fixture
def empty_table(db):
    SupersetReplicatedTable.objects.all().delete()


@pytest.mark.django_db
@pytest.mark.usefixtures("empty_table")
class TestSupersetReplicatedTableAdmin:
    def test_save_model_triggers_sync(self, admin_instance, rf):
        request = _make_request(rf)
        obj = SupersetReplicatedTable(model_label="opportunity.opportunity")

        with mock.patch(
            "commcare_connect.multidb.admin.sync_replicated_tables", return_value=_ok_result()
        ) as mock_sync:
            admin_instance.save_model(request, obj, form=None, change=False)

        mock_sync.assert_called_once()
        assert SupersetReplicatedTable.objects.filter(pk=obj.pk).exists()

    def test_save_model_surfaces_errors(self, admin_instance, rf):
        request = _make_request(rf)
        obj = SupersetReplicatedTable(model_label="opportunity.opportunity")

        with mock.patch(
            "commcare_connect.multidb.admin.sync_replicated_tables",
            return_value=_failed_result(["primary explosion"]),
        ):
            admin_instance.save_model(request, obj, form=None, change=False)

        rendered = [m.message for m in request._messages]
        assert any("primary explosion" in m for m in rendered)
        assert any("setup_logical_replication" in m for m in rendered)

    def test_delete_model_prevents_emptying(self, admin_instance, rf):
        request = _make_request(rf)
        only_entry = SupersetReplicatedTable.objects.create(model_label="opportunity.opportunity")

        with mock.patch("commcare_connect.multidb.admin.sync_replicated_tables") as mock_sync:
            admin_instance.delete_model(request, only_entry)

        mock_sync.assert_not_called()
        assert SupersetReplicatedTable.objects.filter(pk=only_entry.pk).exists()
        rendered = [m.message for m in request._messages]
        assert any("Cannot delete the last" in m for m in rendered)

    def test_delete_model_succeeds_when_others_remain(self, admin_instance, rf):
        request = _make_request(rf)
        keeper = SupersetReplicatedTable.objects.create(model_label="opportunity.opportunity")
        target = SupersetReplicatedTable.objects.create(model_label="users.user")

        with mock.patch(
            "commcare_connect.multidb.admin.sync_replicated_tables", return_value=_ok_result()
        ) as mock_sync:
            admin_instance.delete_model(request, target)

        mock_sync.assert_called_once()
        assert not SupersetReplicatedTable.objects.filter(pk=target.pk).exists()
        assert SupersetReplicatedTable.objects.filter(pk=keeper.pk).exists()

    def test_delete_queryset_prevents_emptying(self, admin_instance, rf):
        request = _make_request(rf)
        SupersetReplicatedTable.objects.create(model_label="opportunity.opportunity")
        SupersetReplicatedTable.objects.create(model_label="users.user")
        all_entries = SupersetReplicatedTable.objects.all()

        with mock.patch("commcare_connect.multidb.admin.sync_replicated_tables") as mock_sync:
            admin_instance.delete_queryset(request, all_entries)

        mock_sync.assert_not_called()
        assert SupersetReplicatedTable.objects.count() == 2
        rendered = [m.message for m in request._messages]
        assert any("Cannot delete every" in m for m in rendered)
