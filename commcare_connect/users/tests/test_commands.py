import tempfile
from io import StringIO
from unittest import mock

import httpx
import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from commcare_connect.connect_id_client.models import ConnectIdUser
from commcare_connect.opportunity.tests.factories import OpportunityAccessFactory, OpportunityFactory
from commcare_connect.users.management.commands.backfill_hq_user_uuid import Command
from commcare_connect.users.tests.factories import ConnectIdUserLinkFactory, MobileUserFactory

FETCH_HQ_USER_UUIDS = "commcare_connect.users.management.commands.backfill_hq_user_uuid.fetch_hq_user_uuids"


@pytest.mark.django_db
class TestPromoteUserToSuperuser:
    def test_promotes_user(self, user):
        assert not user.is_superuser
        assert not user.is_staff

        call_command("promote_user_to_superuser", user.email)

        user.refresh_from_db()
        assert user.is_superuser
        assert user.is_staff

    def test_raises_error_for_unknown_email(self):
        with pytest.raises(CommandError, match="No user with email"):
            call_command("promote_user_to_superuser", "nobody@example.com")


@pytest.mark.django_db
class TestBackfillHqUserUuid:
    def _link_for(self, opportunity, **kwargs):
        return ConnectIdUserLinkFactory(
            hq_server=opportunity.api_key.hq_server,
            domain=opportunity.deliver_app.cc_domain,
            hq_user_uuid=None,
            **kwargs,
        )

    def test_api_keys_keyed_by_server_and_domain(self):
        opportunity = OpportunityFactory()

        api_keys = Command()._api_keys_by_server_and_domain()

        server_and_domain = (opportunity.api_key.hq_server_id, opportunity.deliver_app.cc_domain)
        assert api_keys[server_and_domain] == opportunity.api_key

    def test_backfills_missing_uuid_and_writes_reference_file(self, monkeypatch, tmp_path):
        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
        opportunity = OpportunityFactory()
        link = self._link_for(opportunity)
        uuids = {link.commcare_username: "hq-uuid-1"}

        with mock.patch(FETCH_HQ_USER_UUIDS, return_value=uuids), mock.patch("builtins.input", return_value="y"):
            call_command("backfill_hq_user_uuid")

        link.refresh_from_db()
        assert link.hq_user_uuid == "hq-uuid-1"
        reference_files = list(tmp_path.glob("hq_user_uuid_backfill_*.csv"))
        assert len(reference_files) == 1
        assert str(link.user_id) in reference_files[0].read_text()

    def test_single_call_resolves_all_users_in_a_domain(self, monkeypatch, tmp_path):
        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
        opportunity = OpportunityFactory()
        link1 = self._link_for(opportunity)
        link2 = self._link_for(opportunity)
        uuids = {link1.commcare_username: "u1", link2.commcare_username: "u2"}

        with (
            mock.patch(FETCH_HQ_USER_UUIDS, return_value=uuids) as fetch,
            mock.patch("builtins.input", return_value="y"),
        ):
            call_command("backfill_hq_user_uuid")

        assert fetch.call_count == 1
        link1.refresh_from_db()
        link2.refresh_from_db()
        assert link1.hq_user_uuid == "u1"
        assert link2.hq_user_uuid == "u2"

    def test_shared_domain_across_opportunities_uses_one_call(self, monkeypatch, tmp_path):
        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
        opportunity = OpportunityFactory()
        OpportunityFactory(
            api_key=opportunity.api_key,
            deliver_app=opportunity.deliver_app,
            organization=opportunity.organization,
        )
        link = self._link_for(opportunity)

        with (
            mock.patch(FETCH_HQ_USER_UUIDS, return_value={link.commcare_username: "u1"}) as fetch,
            mock.patch("builtins.input", return_value="y"),
        ):
            call_command("backfill_hq_user_uuid")

        assert fetch.call_count == 1

    def test_separate_domains_get_separate_calls(self, monkeypatch, tmp_path):
        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
        opportunity1 = OpportunityFactory()
        opportunity2 = OpportunityFactory()
        link1 = self._link_for(opportunity1)
        link2 = self._link_for(opportunity2)

        def by_domain(api_key, domain):
            if domain == opportunity1.deliver_app.cc_domain:
                return {link1.commcare_username: "u1"}
            return {link2.commcare_username: "u2"}

        with (
            mock.patch(FETCH_HQ_USER_UUIDS, side_effect=by_domain) as fetch,
            mock.patch("builtins.input", return_value="y"),
        ):
            call_command("backfill_hq_user_uuid")

        assert fetch.call_count == 2
        link1.refresh_from_db()
        link2.refresh_from_db()
        assert link1.hq_user_uuid == "u1"
        assert link2.hq_user_uuid == "u2"

    def test_saves_in_batches(self, monkeypatch, tmp_path):
        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
        opportunity = OpportunityFactory()
        links = [self._link_for(opportunity) for _ in range(3)]
        uuids = {link.commcare_username: f"u{i}" for i, link in enumerate(links)}
        out = StringIO()

        with mock.patch(FETCH_HQ_USER_UUIDS, return_value=uuids), mock.patch("builtins.input", return_value="y"):
            call_command("backfill_hq_user_uuid", "--batch-size", "2", stdout=out)

        for link in links:
            link.refresh_from_db()
        assert all(link.hq_user_uuid for link in links)
        assert "Updated 2/3 records." in out.getvalue()
        assert "Updated 3/3 records." in out.getvalue()

    def test_dry_run_does_not_update(self, monkeypatch, tmp_path):
        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
        opportunity = OpportunityFactory()
        link = self._link_for(opportunity)

        with (
            mock.patch(FETCH_HQ_USER_UUIDS, return_value={link.commcare_username: "u1"}),
            mock.patch("builtins.input", return_value="y"),
        ):
            call_command("backfill_hq_user_uuid", "--dry-run")

        link.refresh_from_db()
        assert not link.hq_user_uuid

    def test_aborts_before_lookups_when_declined(self, monkeypatch, tmp_path):
        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
        opportunity = OpportunityFactory()
        link = self._link_for(opportunity)

        with mock.patch(FETCH_HQ_USER_UUIDS) as fetch, mock.patch("builtins.input", return_value="n"):
            call_command("backfill_hq_user_uuid")

        fetch.assert_not_called()
        link.refresh_from_db()
        assert not link.hq_user_uuid

    def test_aborts_before_save_when_declined(self, monkeypatch, tmp_path):
        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
        opportunity = OpportunityFactory()
        link = self._link_for(opportunity)
        out = StringIO()

        with (
            mock.patch(FETCH_HQ_USER_UUIDS, return_value={link.commcare_username: "u1"}) as fetch,
            mock.patch("builtins.input", side_effect=["y", "n"]),
        ):
            call_command("backfill_hq_user_uuid", stdout=out)

        fetch.assert_called_once()
        link.refresh_from_db()
        assert not link.hq_user_uuid
        assert list(tmp_path.glob("hq_user_uuid_backfill_*.csv"))
        assert "Aborted before saving." in out.getvalue()
        assert "Updated" not in out.getvalue()


FETCH_USERS = "commcare_connect.users.management.commands.refresh_worker_names.fetch_users"


def _worker(phone_number, name):
    user = MobileUserFactory(name=name, phone_number=phone_number)
    OpportunityAccessFactory(user=user)
    return user


def _connectid_user(user, name):
    return ConnectIdUser(name=name, username=user.username, phone_number=user.phone_number)


@pytest.mark.django_db
class TestRefreshWorkerNames:
    def test_updates_only_names_that_differ(self):
        renamed = _worker("+15550001", "Old Name")
        unchanged = _worker("+15550002", "Same Name")
        found = [_connectid_user(renamed, "New Name"), _connectid_user(unchanged, "Same Name")]

        with mock.patch(FETCH_USERS, return_value=found):
            call_command("refresh_worker_names", stdout=StringIO())

        renamed.refresh_from_db()
        unchanged.refresh_from_db()
        assert renamed.name == "New Name"
        assert unchanged.name == "Same Name"

    @pytest.mark.parametrize("batch_size", [0, -1])
    def test_batch_size_below_one_is_rejected_before_any_lookup(self, batch_size):
        worker = _worker("+15550003", "Old Name")

        with mock.patch(FETCH_USERS) as fetch_users:
            with pytest.raises(CommandError):
                call_command("refresh_worker_names", batch_size=batch_size, stdout=StringIO())

        fetch_users.assert_not_called()
        worker.refresh_from_db()
        assert worker.name == "Old Name"

    def test_workers_without_a_phone_number_are_skipped(self):
        no_phone = MobileUserFactory(name="No Phone", phone_number=None)
        OpportunityAccessFactory(user=no_phone)
        out = StringIO()

        with mock.patch(FETCH_USERS, return_value=[]) as fetch_users:
            call_command("refresh_worker_names", stdout=out)

        fetch_users.assert_not_called()
        assert "No workers with a phone number" in out.getvalue()

    @pytest.mark.parametrize(
        "patch_kwargs,stream,expected",
        [
            pytest.param(lambda worker: {"return_value": []}, "stdout", "1 not found in ConnectID", id="not_found"),
            # ConnectID allows a blank name; it must not blank out the local copy.
            pytest.param(
                lambda worker: {"return_value": [_connectid_user(worker, "")]},
                "stderr",
                "Skipping",
                id="blank_name",
            ),
            pytest.param(
                lambda worker: {"side_effect": httpx.HTTPError("boom")},
                "stderr",
                "skipping batch",
                id="lookup_failed",
            ),
        ],
    )
    def test_unusable_connectid_response_leaves_the_local_name_alone(self, patch_kwargs, stream, expected):
        worker = _worker("+15550004", "Old Name")
        streams = {"stdout": StringIO(), "stderr": StringIO()}

        with mock.patch(FETCH_USERS, **patch_kwargs(worker)):
            call_command("refresh_worker_names", **streams)

        worker.refresh_from_db()
        assert worker.name == "Old Name"
        assert expected in streams[stream].getvalue()
