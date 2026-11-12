import uuid
from unittest.mock import patch

import pytest

from commcare_connect.commcarehq.tests.factories import HQServerFactory
from commcare_connect.commcarehq.tests.utils import make_commcare_case
from commcare_connect.microplanning.models import WorkAreaStatus
from commcare_connect.microplanning.services import (
    assign_work_areas_and_sync_to_hq,
    bulk_create_or_update_cases_by_work_areas,
    create_or_update_case_by_work_area,
    unassign_work_areas_for_opportunity,
)
from commcare_connect.microplanning.tests.factories import WorkAreaFactory, WorkAreaGroupFactory
from commcare_connect.opportunity.tests.factories import HQApiKeyFactory, OpportunityAccessFactory, OpportunityFactory
from commcare_connect.users.models import ConnectIDUserLink
from commcare_connect.users.tests.factories import ConnectIdUserLinkFactory
from commcare_connect.utils.commcarehq_api import CommCareHQAPIException


@pytest.mark.django_db
class TestAssignWorkAreasAndSyncToHQ:
    @patch("commcare_connect.microplanning.services.HQ_ASSIGN_BULK_CHUNK_SIZE", 2)
    @patch("commcare_connect.microplanning.services.bulk_create_or_update_cases_by_work_areas")
    def test_failed_chunk_rolls_back_only_its_own_rows(self, mock_bulk_hq, org_user_admin, opportunity):
        """A failed HQ chunk rolls back only its own rows; the chunks that synced commit.
        This is the DB/HQ divergence the per-chunk savepoints prevent."""
        access = OpportunityAccessFactory(opportunity=opportunity)
        # Chunk size 2 => the 5 areas split into chunks of 2, 2 and 1; only the middle chunk fails.
        work_areas = WorkAreaFactory.create_batch(5, opportunity=opportunity)
        ok_a, ok_b, fail_a, fail_b, ok_c = work_areas
        for wa in work_areas:
            wa.opportunity_access = access
            wa.status = WorkAreaStatus.NOT_VISITED
        mock_bulk_hq.side_effect = [None, CommCareHQAPIException("HQ down"), None]

        res = assign_work_areas_and_sync_to_hq(opportunity, work_areas, org_user_admin)

        assert [len(call.args[0]) for call in mock_bulk_hq.call_args_list] == [2, 2, 1]
        assert set(res["assigned_ids"]) == {ok_a.id, ok_b.id, ok_c.id}
        assert set(res["failed_ids"]) == {fail_a.id, fail_b.id}

        for wa in (fail_a, fail_b):
            wa.refresh_from_db()
            assert wa.opportunity_access is None
            assert wa.status == WorkAreaStatus.UNASSIGNED
        for wa in (ok_a, ok_b, ok_c):
            wa.refresh_from_db()
            assert wa.opportunity_access == access
            assert wa.status == WorkAreaStatus.NOT_VISITED


@pytest.mark.django_db
class TestUnassignWorkAreas:
    @patch("commcare_connect.microplanning.services.bulk_create_or_update_cases")
    def test_happy_path_unassigns_and_syncs_to_hq(self, mock_bulk_hq, org_user_admin, opportunity):
        access = OpportunityAccessFactory(opportunity=opportunity)
        group = WorkAreaGroupFactory(opportunity=opportunity)
        work_areas = WorkAreaFactory.create_batch(
            2,
            opportunity=opportunity,
            opportunity_access=access,
            status=WorkAreaStatus.NOT_VISITED,
            work_area_group=group,
        )

        res = unassign_work_areas_for_opportunity(
            opportunity=opportunity,
            work_area_ids=[wa.id for wa in work_areas],
            user=org_user_admin,
        )
        assert set(res["unassigned_ids"]) == {wa.id for wa in work_areas}
        assert res["skipped"] == 0
        assert res["failed_ids"] == []

        for wa in work_areas:
            wa.refresh_from_db()
            assert wa.status == WorkAreaStatus.UNASSIGNED
            assert wa.opportunity_access is None
            assert wa.work_area_group == group  # group is preserved (unlike exclude)

        assert mock_bulk_hq.call_count == 1
        sent_updates = mock_bulk_hq.call_args.args[2]
        assert all(u["owner_id"] == "-" for u in sent_updates)

    @patch("commcare_connect.microplanning.services.bulk_create_or_update_cases")
    def test_already_unassigned_areas_are_skipped(self, mock_bulk_hq, org_user_admin, opportunity):
        wa_unassigned = WorkAreaFactory(
            opportunity=opportunity, opportunity_access=None, status=WorkAreaStatus.UNASSIGNED
        )

        res = unassign_work_areas_for_opportunity(
            opportunity=opportunity,
            work_area_ids=[wa_unassigned.id],
            user=org_user_admin,
        )
        assert res["unassigned_ids"] == []
        assert res["skipped"] == 1
        mock_bulk_hq.assert_not_called()

    @patch("commcare_connect.microplanning.services.bulk_create_or_update_cases")
    def test_excluded_areas_are_skipped(self, mock_bulk_hq, org_user_admin, opportunity):
        access = OpportunityAccessFactory(opportunity=opportunity)
        wa_excluded = WorkAreaFactory(
            opportunity=opportunity, opportunity_access=access, status=WorkAreaStatus.EXCLUDED
        )

        res = unassign_work_areas_for_opportunity(
            opportunity=opportunity,
            work_area_ids=[wa_excluded.id],
            user=org_user_admin,
        )
        assert res["unassigned_ids"] == []
        assert res["skipped"] == 1
        wa_excluded.refresh_from_db()
        assert wa_excluded.status == WorkAreaStatus.EXCLUDED
        assert wa_excluded.opportunity_access == access
        mock_bulk_hq.assert_not_called()

    @patch("commcare_connect.microplanning.services.bulk_create_or_update_cases")
    def test_only_not_visited_assigned_areas_are_unassigned(self, mock_bulk_hq, org_user_admin, opportunity):
        access = OpportunityAccessFactory(opportunity=opportunity)
        wa_not_visited = WorkAreaFactory(
            opportunity=opportunity, opportunity_access=access, status=WorkAreaStatus.NOT_VISITED
        )
        wa_visited = WorkAreaFactory(opportunity=opportunity, opportunity_access=access, status=WorkAreaStatus.VISITED)
        wa_reached = WorkAreaFactory(
            opportunity=opportunity, opportunity_access=access, status=WorkAreaStatus.EXPECTED_VISIT_REACHED
        )
        wa_unassigned = WorkAreaFactory(
            opportunity=opportunity, opportunity_access=None, status=WorkAreaStatus.UNASSIGNED
        )
        wa_excluded = WorkAreaFactory(
            opportunity=opportunity, opportunity_access=access, status=WorkAreaStatus.EXCLUDED
        )

        res = unassign_work_areas_for_opportunity(
            opportunity=opportunity,
            work_area_ids=[wa_not_visited.id, wa_visited.id, wa_reached.id, wa_unassigned.id, wa_excluded.id],
            user=org_user_admin,
        )
        # Only the assigned, not-yet-visited area is unassigned; started/terminal areas are skipped.
        assert res["unassigned_ids"] == [wa_not_visited.id]
        assert res["skipped"] == 4

        wa_not_visited.refresh_from_db()
        assert wa_not_visited.status == WorkAreaStatus.UNASSIGNED
        assert wa_not_visited.opportunity_access is None

        for wa, expected_status in [
            (wa_visited, WorkAreaStatus.VISITED),
            (wa_reached, WorkAreaStatus.EXPECTED_VISIT_REACHED),
        ]:
            wa.refresh_from_db()
            assert wa.status == expected_status
            assert wa.opportunity_access == access

    @patch("commcare_connect.microplanning.services.bulk_create_or_update_cases")
    def test_hq_failure_rolls_back_chunk(self, mock_bulk_hq, org_user_admin, opportunity):
        access = OpportunityAccessFactory(opportunity=opportunity)
        work_areas = WorkAreaFactory.create_batch(
            2,
            opportunity=opportunity,
            opportunity_access=access,
            status=WorkAreaStatus.NOT_VISITED,
        )
        mock_bulk_hq.side_effect = CommCareHQAPIException("HQ down")

        res = unassign_work_areas_for_opportunity(
            opportunity=opportunity,
            work_area_ids=[wa.id for wa in work_areas],
            user=org_user_admin,
        )
        assert res["unassigned_ids"] == []
        assert set(res["failed_ids"]) == {wa.id for wa in work_areas}

        for wa in work_areas:
            wa.refresh_from_db()
            assert wa.status == WorkAreaStatus.NOT_VISITED
            assert wa.opportunity_access == access

    @patch("commcare_connect.microplanning.services.bulk_create_or_update_cases")
    def test_no_case_id_unassigns_locally_without_hq_call(self, mock_bulk_hq, org_user_admin, opportunity):
        access = OpportunityAccessFactory(opportunity=opportunity)
        wa = WorkAreaFactory(
            opportunity=opportunity,
            opportunity_access=access,
            status=WorkAreaStatus.NOT_VISITED,
            case_id=None,
        )

        unassign_work_areas_for_opportunity(
            opportunity=opportunity,
            work_area_ids=[wa.id],
            user=org_user_admin,
        )

        mock_bulk_hq.assert_not_called()
        wa.refresh_from_db()
        assert wa.status == WorkAreaStatus.UNASSIGNED
        assert wa.opportunity_access is None

    @patch("commcare_connect.microplanning.services.bulk_create_or_update_cases")
    def test_work_area_from_other_opportunity_is_ignored(self, mock_bulk_hq, org_user_admin, opportunity):
        other_access = OpportunityAccessFactory()
        other_wa = WorkAreaFactory(
            opportunity=other_access.opportunity,
            opportunity_access=other_access,
            status=WorkAreaStatus.NOT_VISITED,
        )

        unassign_work_areas_for_opportunity(
            opportunity=opportunity,
            work_area_ids=[other_wa.id],
            user=org_user_admin,
        )

        other_wa.refresh_from_db()
        assert other_wa.status == WorkAreaStatus.NOT_VISITED
        assert other_wa.opportunity_access == other_access
        mock_bulk_hq.assert_not_called()

    @patch("commcare_connect.microplanning.services.HQ_UNASSIGN_BULK_CHUNK_SIZE", 50)
    @patch("commcare_connect.microplanning.services.bulk_create_or_update_cases")
    def test_chunking_splits_large_batches(self, mock_bulk_hq, org_user_admin, opportunity):
        chunk_size = 50
        access = OpportunityAccessFactory(opportunity=opportunity)
        count = chunk_size * 2 + 25
        work_areas = WorkAreaFactory.create_batch(
            count,
            opportunity=opportunity,
            opportunity_access=access,
            status=WorkAreaStatus.NOT_VISITED,
        )

        unassign_work_areas_for_opportunity(
            opportunity=opportunity,
            work_area_ids=[wa.id for wa in work_areas],
            user=org_user_admin,
        )

        assert mock_bulk_hq.call_count == 3
        chunk_sizes = [len(call.args[2]) for call in mock_bulk_hq.call_args_list]
        assert chunk_sizes == [chunk_size, chunk_size, 25]

    @patch("commcare_connect.microplanning.services.bulk_create_or_update_cases")
    def test_duplicate_ids_are_deduped(self, mock_bulk_hq, org_user_admin, opportunity):
        """Passing the same work area ID twice should only unassign + HQ-update it once."""
        access = OpportunityAccessFactory(opportunity=opportunity)
        wa = WorkAreaFactory(opportunity=opportunity, opportunity_access=access, status=WorkAreaStatus.NOT_VISITED)

        res = unassign_work_areas_for_opportunity(
            opportunity=opportunity,
            work_area_ids=[wa.id, wa.id, wa.id],
            user=org_user_admin,
        )

        assert res["unassigned_ids"] == [wa.id]
        assert res["skipped"] == 0
        assert mock_bulk_hq.call_count == 1
        assert len(mock_bulk_hq.call_args.args[2]) == 1

    @patch("commcare_connect.microplanning.services.HQ_UNASSIGN_BULK_CHUNK_SIZE", 50)
    @patch("commcare_connect.microplanning.services.bulk_create_or_update_cases")
    def test_failed_batch_does_not_block_other_batches(self, mock_bulk_hq, org_user_admin, opportunity):
        """A failed HQ batch rolls back only its own chunk; other batches still succeed."""
        chunk_size = 50
        access = OpportunityAccessFactory(opportunity=opportunity)
        count = chunk_size * 3
        work_areas = WorkAreaFactory.create_batch(
            count,
            opportunity=opportunity,
            opportunity_access=access,
            status=WorkAreaStatus.NOT_VISITED,
        )
        # Second of three chunks fails; the first and third commit independently.
        mock_bulk_hq.side_effect = [None, CommCareHQAPIException("HQ down"), None]

        result = unassign_work_areas_for_opportunity(
            opportunity=opportunity,
            work_area_ids=[wa.id for wa in work_areas],
            user=org_user_admin,
        )

        assert len(result["unassigned_ids"]) == 2 * chunk_size
        assert len(result["failed_ids"]) == chunk_size
        # The failed batch's areas stay assigned; nothing leaks between the success/failure sets.
        assert set(result["unassigned_ids"]).isdisjoint(result["failed_ids"])

        for wa in work_areas:
            wa.refresh_from_db()
        unassigned = {wa.id for wa in work_areas if wa.status == WorkAreaStatus.UNASSIGNED}
        still_assigned = {wa.id for wa in work_areas if wa.status == WorkAreaStatus.NOT_VISITED}
        assert unassigned == set(result["unassigned_ids"])
        assert still_assigned == set(result["failed_ids"])


@pytest.mark.django_db
class TestCreateOrUpdateCaseByWorkArea:
    def _make_work_area(self, case_id=None):
        api_key = HQApiKeyFactory(hq_server=HQServerFactory())
        opp_access = OpportunityAccessFactory(opportunity__api_key=api_key)
        work_area_group = WorkAreaGroupFactory(opportunity=opp_access.opportunity)
        return WorkAreaFactory(
            opportunity=opp_access.opportunity,
            work_area_group=work_area_group,
            opportunity_access=opp_access,
            case_id=case_id,
        )

    def _make_work_area_link(self, work_area, hq_user_uuid=None):
        opp_access = work_area.opportunity_access
        return _make_link(opportunity=opp_access.opportunity, user=opp_access.user, hq_user_uuid=hq_user_uuid)

    @pytest.mark.parametrize("has_case_id", [False, True])
    def test_uses_stored_hq_user_uuid_when_available(self, has_case_id):
        existing_case_id = str(uuid.uuid4()) if has_case_id else None
        work_area = self._make_work_area(case_id=existing_case_id)

        hq_user_uuid = "stored-uuid"
        self._make_work_area_link(work_area, hq_user_uuid=hq_user_uuid)
        work_area_case = make_commcare_case(case_id=str(existing_case_id or str(uuid.uuid4())))

        with (
            patch("commcare_connect.commcarehq.api.fetch_hq_user_uuid") as mock_fetch,
            patch(
                "commcare_connect.microplanning.services.create_or_update_case", return_value=work_area_case
            ) as mock_create_or_update,
        ):
            create_or_update_case_by_work_area(work_area)

        mock_fetch.assert_not_called()
        call_args, call_kwargs = mock_create_or_update.call_args
        assert call_args[2]["owner_id"] == hq_user_uuid
        assert call_kwargs.get("case_id") == existing_case_id

    def test_fetches_and_persists_uuid_when_link_has_none(self):
        work_area = self._make_work_area()
        link = self._make_work_area_link(work_area)

        fetched_uuid = "fetched-uuid"
        work_area_case = make_commcare_case()

        with (
            patch("commcare_connect.commcarehq.api.fetch_hq_user_uuid", return_value=fetched_uuid) as mock_fetch,
            patch(
                "commcare_connect.microplanning.services.create_or_update_case", return_value=work_area_case
            ) as mock_create_or_update,
        ):
            create_or_update_case_by_work_area(work_area)

        mock_fetch.assert_called_once()
        call_args, _ = mock_create_or_update.call_args
        assert call_args[2]["owner_id"] == fetched_uuid
        link.refresh_from_db()
        assert link.hq_user_uuid == fetched_uuid

    def test_raises_when_link_does_not_exist(self):
        work_area = self._make_work_area()

        with pytest.raises(ConnectIDUserLink.DoesNotExist):
            create_or_update_case_by_work_area(work_area)

    def test_raises_when_user_not_found_on_hq(self):
        work_area = self._make_work_area()
        self._make_work_area_link(work_area)

        with patch("commcare_connect.commcarehq.api.fetch_hq_user_uuid", return_value=None):
            with pytest.raises(CommCareHQAPIException, match="Failed to find HQ user"):
                create_or_update_case_by_work_area(work_area)

    def test_no_opportunity_access(self):
        work_area = WorkAreaFactory()

        with pytest.raises(ValueError, match="Work Area must have an assigned Opportunity Access"):
            create_or_update_case_by_work_area(work_area)


@pytest.mark.django_db
class TestBulkCreateOrUpdateCasesByWorkAreas:
    def _make_opportunity_with_work_area(self):
        api_key = HQApiKeyFactory(hq_server=HQServerFactory())
        opp_access = OpportunityAccessFactory(opportunity__api_key=api_key)
        wa_group = WorkAreaGroupFactory(opportunity=opp_access.opportunity)
        work_area = WorkAreaFactory(
            opportunity=opp_access.opportunity,
            work_area_group=wa_group,
            opportunity_access=opp_access,
        )
        return opp_access.opportunity, work_area

    def test_uses_stored_uuid_and_fetches_missing(self):
        opportunity, wa_with_stored = self._make_opportunity_with_work_area()
        wa_group = wa_with_stored.work_area_group
        access_to_fetch = OpportunityAccessFactory(opportunity=opportunity)
        wa_to_fetch = WorkAreaFactory(
            opportunity=opportunity, work_area_group=wa_group, opportunity_access=access_to_fetch
        )

        stored_uuid = "stored-uuid"
        fetched_uuid = "fetched-uuid"
        _make_link(opportunity=opportunity, user=wa_with_stored.opportunity_access.user, hq_user_uuid=stored_uuid)
        link_to_backfill = _make_link(opportunity=opportunity, user=access_to_fetch.user)

        returned_cases = [make_commcare_case(), make_commcare_case()]
        with (
            patch("commcare_connect.commcarehq.api.fetch_hq_user_uuid", return_value=fetched_uuid) as mock_fetch,
            patch(
                "commcare_connect.microplanning.services.bulk_create_or_update_cases", return_value=returned_cases
            ) as mock_bulk,
        ):
            bulk_create_or_update_cases_by_work_areas([wa_with_stored, wa_to_fetch], opportunity)

        mock_fetch.assert_called_once()
        assert mock_bulk.call_args.kwargs["create"] is None  # UPSERT keyed on external_id
        sent_cases = mock_bulk.call_args[0][2]
        owners_by_external_id = {c["external_id"]: c["owner_id"] for c in sent_cases}
        assert owners_by_external_id[str(wa_with_stored.id)] == stored_uuid
        assert owners_by_external_id[str(wa_to_fetch.id)] == fetched_uuid
        link_to_backfill.refresh_from_db()
        assert link_to_backfill.hq_user_uuid == fetched_uuid

    def test_raises_when_user_not_found_on_hq(self):
        opportunity, work_area = self._make_opportunity_with_work_area()
        _make_link(opportunity=opportunity, user=work_area.opportunity_access.user)

        with patch("commcare_connect.commcarehq.api.fetch_hq_user_uuid", return_value=None):
            with pytest.raises(CommCareHQAPIException, match="Failed to find HQ user"):
                bulk_create_or_update_cases_by_work_areas([work_area], opportunity)

    def test_returns_empty_for_no_work_areas(self):
        opportunity, _ = self._make_opportunity_with_work_area()

        assert bulk_create_or_update_cases_by_work_areas([], opportunity) == []

    def test_matches_cases_to_work_areas_by_external_id(self):
        # Regression: HQ may return cases in a different order than they were
        # sent. The function must match each returned case back to its
        # WorkArea via external_id, not by list position.
        api_key = HQApiKeyFactory(hq_server=HQServerFactory())
        opportunity = OpportunityFactory(api_key=api_key)
        work_areas = [
            WorkAreaFactory(
                opportunity=opportunity,
                opportunity_access=OpportunityAccessFactory(opportunity=opportunity),
                case_id=None,
            )
            for _ in range(3)
        ]
        for wa in work_areas:
            _make_link(opportunity=opportunity, user=wa.opportunity_access.user, hq_user_uuid="stored-uuid")

        # Build HQ response in reversed order to prove ordering is not assumed.
        expected_case_ids = {str(wa.pk): str(uuid.uuid4()) for wa in work_areas}
        returned_cases = [
            make_commcare_case(case_id=expected_case_ids[str(wa.pk)], external_id=str(wa.pk))
            for wa in reversed(work_areas)
        ]

        with patch(
            "commcare_connect.microplanning.services.bulk_create_or_update_cases",
            return_value=returned_cases,
        ):
            bulk_create_or_update_cases_by_work_areas(work_areas, opportunity)

        for wa in work_areas:
            wa.refresh_from_db()
            assert str(wa.case_id) == expected_case_ids[str(wa.pk)]


def _make_link(opportunity, user, hq_user_uuid=None):
    """Link `user` to the HQ domain of the opportunity's deliver app."""
    domain = opportunity.deliver_app.cc_domain
    return ConnectIdUserLinkFactory(
        user=user,
        commcare_username=f"{user.username.lower()}@{domain}.commcarehq.org",
        domain=domain,
        hq_server=opportunity.api_key.hq_server,
        hq_user_uuid=hq_user_uuid,
    )
