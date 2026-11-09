from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError

from commcare_connect.microplanning.models import WorkAreaStatus
from commcare_connect.microplanning.services.work_area_assignment import (
    assign_work_areas_and_sync_to_hq,
    assign_work_areas_to_workers,
    check_assignees,
    unassign_work_areas_for_opportunity,
)
from commcare_connect.microplanning.tests.factories import (
    WorkAreaFactory,
    WorkAreaGroupFactory,
)
from commcare_connect.opportunity.tests.factories import OpportunityAccessFactory
from commcare_connect.utils.commcarehq_api import CommCareHQAPIException


@pytest.mark.django_db
class TestAssignWorkAreasAndSyncToHQ:
    @patch("commcare_connect.microplanning.services.work_area_assignment.HQ_ASSIGN_BULK_CHUNK_SIZE", 2)
    @patch("commcare_connect.microplanning.services.work_area_assignment.bulk_create_or_update_cases_by_work_areas")
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
    @patch("commcare_connect.microplanning.services.work_area_assignment.bulk_create_or_update_cases")
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

    @patch("commcare_connect.microplanning.services.work_area_assignment.bulk_create_or_update_cases")
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

    @patch("commcare_connect.microplanning.services.work_area_assignment.bulk_create_or_update_cases")
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

    @patch("commcare_connect.microplanning.services.work_area_assignment.bulk_create_or_update_cases")
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

    @patch("commcare_connect.microplanning.services.work_area_assignment.bulk_create_or_update_cases")
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

    @patch("commcare_connect.microplanning.services.work_area_assignment.bulk_create_or_update_cases")
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

    @patch("commcare_connect.microplanning.services.work_area_assignment.bulk_create_or_update_cases")
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

    @patch("commcare_connect.microplanning.services.work_area_assignment.HQ_UNASSIGN_BULK_CHUNK_SIZE", 50)
    @patch("commcare_connect.microplanning.services.work_area_assignment.bulk_create_or_update_cases")
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

    @patch("commcare_connect.microplanning.services.work_area_assignment.bulk_create_or_update_cases")
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

    @patch("commcare_connect.microplanning.services.work_area_assignment.HQ_UNASSIGN_BULK_CHUNK_SIZE", 50)
    @patch("commcare_connect.microplanning.services.work_area_assignment.bulk_create_or_update_cases")
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
class TestAssignWorkAreasToWorkers:
    @patch("commcare_connect.microplanning.tasks.send_work_area_assignment_notification.delay")
    @patch("commcare_connect.microplanning.services.work_area_assignment.bulk_create_or_update_cases_by_work_areas")
    def test_assigns_and_notifies_each_worker_once(
        self, mock_bulk_hq, notify, org_user_admin, opportunity, django_capture_on_commit_callbacks
    ):
        access_a = OpportunityAccessFactory(opportunity=opportunity)
        access_b = OpportunityAccessFactory(opportunity=opportunity)
        wa1, wa2, wa3 = WorkAreaFactory.create_batch(3, opportunity=opportunity, status=WorkAreaStatus.UNASSIGNED)

        with django_capture_on_commit_callbacks(execute=True):
            result = assign_work_areas_to_workers(
                opportunity=opportunity,
                work_area_ids_by_access_id={access_a.id: [wa1.id, wa2.id], access_b.id: [wa3.id]},
                user=org_user_admin,
            )

        assert sorted(result["assigned_ids"]) == sorted([wa1.id, wa2.id, wa3.id])
        for work_area, access in ((wa1, access_a), (wa2, access_a), (wa3, access_b)):
            work_area.refresh_from_db()
            assert work_area.opportunity_access_id == access.id
            assert work_area.status == WorkAreaStatus.NOT_VISITED
        assert sorted(call.args[0] for call in notify.call_args_list) == sorted([access_a.id, access_b.id])

    def test_work_area_outside_the_opportunity_is_rejected(self, org_user_admin, opportunity):
        access = OpportunityAccessFactory(opportunity=opportunity)
        other_work_area = WorkAreaFactory()

        with pytest.raises(ValidationError, match="Invalid work area IDs"):
            assign_work_areas_to_workers(
                opportunity=opportunity,
                work_area_ids_by_access_id={access.id: [other_work_area.id]},
                user=org_user_admin,
            )


@pytest.mark.django_db
class TestCheckAssignees:
    def test_returns_the_accesses_by_id(self, opportunity):
        access = OpportunityAccessFactory(opportunity=opportunity)

        assert check_assignees(opportunity=opportunity, access_ids={access.id}) == {access.id: access}

    def test_access_from_another_opportunity_is_rejected(self, opportunity):
        other_access = OpportunityAccessFactory()

        with pytest.raises(ValidationError, match="Invalid assignee IDs"):
            check_assignees(opportunity=opportunity, access_ids={other_access.id})
