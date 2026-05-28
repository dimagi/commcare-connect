from unittest.mock import patch

import pytest

from commcare_connect.microplanning.const import HQ_BULK_CHUNK_SIZE
from commcare_connect.microplanning.helpers import (
    exclude_work_areas_for_opportunity,
    unassign_work_areas_for_opportunity,
)
from commcare_connect.microplanning.models import WorkAreaStatus
from commcare_connect.microplanning.tests.factories import WorkAreaFactory, WorkAreaGroupFactory
from commcare_connect.opportunity.tests.factories import OpportunityAccessFactory
from commcare_connect.utils.commcarehq_api import CommCareHQAPIException


@pytest.mark.django_db
class TestExcludeWorkAreas:
    @patch("commcare_connect.microplanning.helpers.bulk_create_or_update_cases")
    def test_happy_path_excludes_not_visited_areas(self, mock_bulk_hq, org_user_admin, opportunity):
        access = OpportunityAccessFactory(opportunity=opportunity)
        group = WorkAreaGroupFactory(opportunity=opportunity)
        work_areas = WorkAreaFactory.create_batch(
            2,
            opportunity=opportunity,
            opportunity_access=access,
            status=WorkAreaStatus.NOT_VISITED,
            work_area_group=group,
        )

        res = exclude_work_areas_for_opportunity(
            opportunity=opportunity,
            work_area_ids=[wa.id for wa in work_areas],
            user=org_user_admin,
            exclusion_reason="Flooding",
        )
        assert set(res["excluded_ids"]) == {wa.id for wa in work_areas}
        assert res["skipped"] == 0
        assert res["failed"] == 0

        for wa in work_areas:
            wa.refresh_from_db()
            assert wa.status == WorkAreaStatus.EXCLUDED
            assert wa.work_area_group is None
            assert wa.excluded_by == org_user_admin
            assert wa.excluded_reason == "Flooding"

        assert mock_bulk_hq.call_count == 1

    @patch("commcare_connect.microplanning.helpers.bulk_create_or_update_cases")
    def test_mixed_batch_only_not_visited_is_excluded(self, mock_bulk_hq, org_user_admin, opportunity):
        wa_valid = WorkAreaFactory(opportunity=opportunity, status=WorkAreaStatus.NOT_VISITED)
        wa_inaccessible = WorkAreaFactory(opportunity=opportunity, status=WorkAreaStatus.INACCESSIBLE)
        wa_excluded = WorkAreaFactory(opportunity=opportunity, status=WorkAreaStatus.EXCLUDED)

        res = exclude_work_areas_for_opportunity(
            opportunity=opportunity,
            work_area_ids=[wa_valid.id, wa_inaccessible.id, wa_excluded.id],
            user=org_user_admin,
            exclusion_reason="Test",
        )
        assert res["excluded_ids"] == [wa_valid.id]
        assert res["skipped"] == 2
        assert res["failed"] == 0

        wa_valid.refresh_from_db()
        wa_inaccessible.refresh_from_db()
        wa_excluded.refresh_from_db()

        assert wa_valid.status == WorkAreaStatus.EXCLUDED
        assert wa_inaccessible.status == WorkAreaStatus.INACCESSIBLE  # unchanged
        assert wa_excluded.status == WorkAreaStatus.EXCLUDED  # unchanged

    @patch("commcare_connect.microplanning.helpers.bulk_create_or_update_cases")
    def test_hq_batch_failure_skips_local_exclusion_for_whole_chunk(self, mock_bulk_hq, org_user_admin, opportunity):
        """When the HQ bulk call fails, no work area in that chunk is excluded."""
        access = OpportunityAccessFactory(opportunity=opportunity)
        group = WorkAreaGroupFactory(opportunity=opportunity)
        work_areas = WorkAreaFactory.create_batch(
            2,
            opportunity=opportunity,
            status=WorkAreaStatus.NOT_VISITED,
            opportunity_access=access,
            work_area_group=group,
        )
        mock_bulk_hq.side_effect = CommCareHQAPIException("HQ down")

        res = exclude_work_areas_for_opportunity(
            opportunity=opportunity,
            work_area_ids=[wa.id for wa in work_areas],
            user=org_user_admin,
            exclusion_reason="Test",
        )
        assert res["excluded_ids"] == []
        assert res["skipped"] == 0
        assert res["failed"] == 2

        for wa in work_areas:
            wa.refresh_from_db()
            assert wa.status == WorkAreaStatus.NOT_VISITED
            assert wa.work_area_group == group

    @patch("commcare_connect.microplanning.helpers.bulk_create_or_update_cases")
    def test_no_case_id_excludes_locally_without_hq_call(self, mock_bulk_hq, org_user_admin, opportunity):
        wa = WorkAreaFactory(opportunity=opportunity, status=WorkAreaStatus.NOT_VISITED, case_id=None)

        exclude_work_areas_for_opportunity(
            opportunity=opportunity,
            work_area_ids=[wa.id],
            user=org_user_admin,
            exclusion_reason="No case",
        )

        mock_bulk_hq.assert_not_called()
        wa.refresh_from_db()
        assert wa.status == WorkAreaStatus.EXCLUDED

    @patch("commcare_connect.microplanning.helpers.bulk_create_or_update_cases")
    def test_work_area_from_other_opportunity_is_ignored(self, mock_bulk_hq, org_user_admin, opportunity):
        other_wa = WorkAreaFactory(status=WorkAreaStatus.NOT_VISITED)  # different opportunity

        exclude_work_areas_for_opportunity(
            opportunity=opportunity,
            work_area_ids=[other_wa.id],
            user=org_user_admin,
            exclusion_reason="Test",
        )

        other_wa.refresh_from_db()
        assert other_wa.status == WorkAreaStatus.NOT_VISITED  # unchanged
        mock_bulk_hq.assert_not_called()

    @pytest.mark.parametrize(
        "status",
        [
            WorkAreaStatus.VISITED,
            WorkAreaStatus.UNASSIGNED,
            WorkAreaStatus.REQUEST_FOR_INACCESSIBLE,
            WorkAreaStatus.EXPECTED_VISIT_REACHED,
        ],
    )
    @patch("commcare_connect.microplanning.helpers.bulk_create_or_update_cases")
    def test_work_started_statuses_are_not_excluded(self, mock_bulk_hq, org_user_admin, opportunity, status):
        wa = WorkAreaFactory(opportunity=opportunity, status=status)

        exclude_work_areas_for_opportunity(
            opportunity=opportunity,
            work_area_ids=[wa.id],
            user=org_user_admin,
            exclusion_reason="Test",
        )

        wa.refresh_from_db()
        assert wa.status == status  # unchanged
        mock_bulk_hq.assert_not_called()

    @patch("commcare_connect.microplanning.helpers.bulk_create_or_update_cases")
    def test_work_areas_over_chunk_size_are_split_into_batches(self, mock_bulk_hq, org_user_admin, opportunity):
        """125 work areas → 3 HQ calls (50, 50, 25); all excluded on success."""
        access = OpportunityAccessFactory(opportunity=opportunity)
        group = WorkAreaGroupFactory(opportunity=opportunity)
        count = HQ_BULK_CHUNK_SIZE * 2 + 25
        work_areas = WorkAreaFactory.create_batch(
            count,
            opportunity=opportunity,
            status=WorkAreaStatus.NOT_VISITED,
            opportunity_access=access,
            work_area_group=group,
        )

        exclude_work_areas_for_opportunity(
            opportunity=opportunity,
            work_area_ids=[wa.id for wa in work_areas],
            user=org_user_admin,
            exclusion_reason="Flooding",
        )

        assert mock_bulk_hq.call_count == 3
        chunk_sizes = [len(call.args[2]) for call in mock_bulk_hq.call_args_list]
        assert chunk_sizes == [HQ_BULK_CHUNK_SIZE, HQ_BULK_CHUNK_SIZE, 25]

        for wa in work_areas:
            wa.refresh_from_db()
            assert wa.status == WorkAreaStatus.EXCLUDED

    @patch("commcare_connect.microplanning.helpers.bulk_create_or_update_cases")
    def test_one_failed_chunk_does_not_block_other_chunks(self, mock_bulk_hq, org_user_admin, opportunity):
        """Chunk 2 fails; chunks 1 and 3 still excluded."""
        access = OpportunityAccessFactory(opportunity=opportunity)
        group = WorkAreaGroupFactory(opportunity=opportunity)
        count = HQ_BULK_CHUNK_SIZE * 3
        work_areas = WorkAreaFactory.create_batch(
            count,
            opportunity=opportunity,
            status=WorkAreaStatus.NOT_VISITED,
            opportunity_access=access,
            work_area_group=group,
        )

        mock_bulk_hq.side_effect = [None, CommCareHQAPIException("HQ down"), None]

        exclude_work_areas_for_opportunity(
            opportunity=opportunity,
            work_area_ids=[wa.id for wa in work_areas],
            user=org_user_admin,
            exclusion_reason="Test",
        )

        for wa in work_areas:
            wa.refresh_from_db()

        excluded = [wa for wa in work_areas if wa.status == WorkAreaStatus.EXCLUDED]
        not_visited = [wa for wa in work_areas if wa.status == WorkAreaStatus.NOT_VISITED]
        assert len(excluded) == 2 * HQ_BULK_CHUNK_SIZE
        assert len(not_visited) == HQ_BULK_CHUNK_SIZE


@pytest.mark.django_db
class TestUnassignWorkAreas:
    @patch("commcare_connect.microplanning.helpers.bulk_create_or_update_cases")
    def test_happy_path_unassigns_and_syncs_to_hq(self, mock_bulk_hq, org_user_admin, opportunity):
        access = OpportunityAccessFactory(opportunity=opportunity)
        group = WorkAreaGroupFactory(opportunity=opportunity)
        work_areas = WorkAreaFactory.create_batch(
            2,
            opportunity=opportunity,
            opportunity_access=access,
            status=WorkAreaStatus.NOT_STARTED,
            work_area_group=group,
        )

        res = unassign_work_areas_for_opportunity(
            opportunity=opportunity,
            work_area_ids=[wa.id for wa in work_areas],
            user=org_user_admin,
        )
        assert set(res["unassigned_ids"]) == {wa.id for wa in work_areas}
        assert res["skipped"] == 0
        assert res["failed"] == 0

        for wa in work_areas:
            wa.refresh_from_db()
            assert wa.status == WorkAreaStatus.UNASSIGNED
            assert wa.opportunity_access is None
            assert wa.work_area_group == group  # group is preserved (unlike exclude)

        assert mock_bulk_hq.call_count == 1
        sent_updates = mock_bulk_hq.call_args.args[2]
        assert all(u["owner_id"] == "-" for u in sent_updates)
        assert all(u["create"] is False for u in sent_updates)

    @patch("commcare_connect.microplanning.helpers.bulk_create_or_update_cases")
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

    @patch("commcare_connect.microplanning.helpers.bulk_create_or_update_cases")
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

    @patch("commcare_connect.microplanning.helpers.bulk_create_or_update_cases")
    def test_mixed_statuses_only_assigned_non_excluded_are_unassigned(self, mock_bulk_hq, org_user_admin, opportunity):
        access = OpportunityAccessFactory(opportunity=opportunity)
        wa_assigned = WorkAreaFactory(
            opportunity=opportunity, opportunity_access=access, status=WorkAreaStatus.NOT_VISITED
        )
        wa_unassigned = WorkAreaFactory(
            opportunity=opportunity, opportunity_access=None, status=WorkAreaStatus.UNASSIGNED
        )
        wa_excluded = WorkAreaFactory(
            opportunity=opportunity, opportunity_access=access, status=WorkAreaStatus.EXCLUDED
        )
        wa_visited = WorkAreaFactory(opportunity=opportunity, opportunity_access=access, status=WorkAreaStatus.VISITED)

        res = unassign_work_areas_for_opportunity(
            opportunity=opportunity,
            work_area_ids=[wa_assigned.id, wa_unassigned.id, wa_excluded.id, wa_visited.id],
            user=org_user_admin,
        )
        assert set(res["unassigned_ids"]) == {wa_assigned.id, wa_visited.id}
        assert res["skipped"] == 2

        wa_assigned.refresh_from_db()
        wa_visited.refresh_from_db()
        assert wa_assigned.status == WorkAreaStatus.UNASSIGNED
        assert wa_assigned.opportunity_access is None
        assert wa_visited.status == WorkAreaStatus.UNASSIGNED
        assert wa_visited.opportunity_access is None

    @patch("commcare_connect.microplanning.helpers.bulk_create_or_update_cases")
    def test_hq_failure_rolls_back_chunk(self, mock_bulk_hq, org_user_admin, opportunity):
        access = OpportunityAccessFactory(opportunity=opportunity)
        work_areas = WorkAreaFactory.create_batch(
            2,
            opportunity=opportunity,
            opportunity_access=access,
            status=WorkAreaStatus.NOT_STARTED,
        )
        mock_bulk_hq.side_effect = CommCareHQAPIException("HQ down")

        res = unassign_work_areas_for_opportunity(
            opportunity=opportunity,
            work_area_ids=[wa.id for wa in work_areas],
            user=org_user_admin,
        )
        assert res["unassigned_ids"] == []
        assert res["failed"] == 2

        for wa in work_areas:
            wa.refresh_from_db()
            assert wa.status == WorkAreaStatus.NOT_STARTED
            assert wa.opportunity_access == access

    @patch("commcare_connect.microplanning.helpers.bulk_create_or_update_cases")
    def test_no_case_id_unassigns_locally_without_hq_call(self, mock_bulk_hq, org_user_admin, opportunity):
        access = OpportunityAccessFactory(opportunity=opportunity)
        wa = WorkAreaFactory(
            opportunity=opportunity,
            opportunity_access=access,
            status=WorkAreaStatus.NOT_STARTED,
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

    @patch("commcare_connect.microplanning.helpers.bulk_create_or_update_cases")
    def test_work_area_from_other_opportunity_is_ignored(self, mock_bulk_hq, org_user_admin, opportunity):
        other_access = OpportunityAccessFactory()
        other_wa = WorkAreaFactory(
            opportunity=other_access.opportunity,
            opportunity_access=other_access,
            status=WorkAreaStatus.NOT_STARTED,
        )

        unassign_work_areas_for_opportunity(
            opportunity=opportunity,
            work_area_ids=[other_wa.id],
            user=org_user_admin,
        )

        other_wa.refresh_from_db()
        assert other_wa.status == WorkAreaStatus.NOT_STARTED
        assert other_wa.opportunity_access == other_access
        mock_bulk_hq.assert_not_called()

    @patch("commcare_connect.microplanning.helpers.bulk_create_or_update_cases")
    def test_chunking_splits_large_batches(self, mock_bulk_hq, org_user_admin, opportunity):
        access = OpportunityAccessFactory(opportunity=opportunity)
        count = HQ_BULK_CHUNK_SIZE * 2 + 25
        work_areas = WorkAreaFactory.create_batch(
            count,
            opportunity=opportunity,
            opportunity_access=access,
            status=WorkAreaStatus.NOT_STARTED,
        )

        unassign_work_areas_for_opportunity(
            opportunity=opportunity,
            work_area_ids=[wa.id for wa in work_areas],
            user=org_user_admin,
        )

        assert mock_bulk_hq.call_count == 3
        chunk_sizes = [len(call.args[2]) for call in mock_bulk_hq.call_args_list]
        assert chunk_sizes == [HQ_BULK_CHUNK_SIZE, HQ_BULK_CHUNK_SIZE, 25]

    @patch("commcare_connect.microplanning.helpers.bulk_create_or_update_cases")
    def test_one_failed_chunk_does_not_block_others(self, mock_bulk_hq, org_user_admin, opportunity):
        access = OpportunityAccessFactory(opportunity=opportunity)
        count = HQ_BULK_CHUNK_SIZE * 3
        work_areas = WorkAreaFactory.create_batch(
            count,
            opportunity=opportunity,
            opportunity_access=access,
            status=WorkAreaStatus.NOT_STARTED,
        )
        mock_bulk_hq.side_effect = [None, CommCareHQAPIException("HQ down"), None]

        unassign_work_areas_for_opportunity(
            opportunity=opportunity,
            work_area_ids=[wa.id for wa in work_areas],
            user=org_user_admin,
        )

        for wa in work_areas:
            wa.refresh_from_db()

        unassigned = [wa for wa in work_areas if wa.status == WorkAreaStatus.UNASSIGNED]
        still_assigned = [wa for wa in work_areas if wa.status == WorkAreaStatus.NOT_STARTED]
        assert len(unassigned) == 2 * HQ_BULK_CHUNK_SIZE
        assert len(still_assigned) == HQ_BULK_CHUNK_SIZE
