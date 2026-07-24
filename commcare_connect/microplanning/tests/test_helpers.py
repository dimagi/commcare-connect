from unittest.mock import patch

import pytest
from django.contrib.gis.geos import Polygon

from commcare_connect.microplanning.const import HQ_BULK_CHUNK_SIZE
from commcare_connect.microplanning.helpers import (
    exclude_work_areas_for_opportunity,
    unassign_work_areas_for_opportunity,
)
from commcare_connect.microplanning.models import SRID, WorkAreaStatus
from commcare_connect.microplanning.tests.factories import WorkAreaFactory, WorkAreaGroupFactory
from commcare_connect.opportunity.tests.factories import OpportunityAccessFactory
from commcare_connect.utils.commcarehq_api import CommCareHQAPIException


def _unit_square(x, y):
    """A 1x1 square with lower-left corner at (x, y); its centroid is (x + 0.5, y + 0.5)."""
    return Polygon(((x, y), (x, y + 1), (x + 1, y + 1), (x + 1, y), (x, y)), srid=SRID)


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
    def test_mixed_batch_eligible_and_ineligible(self, mock_bulk_hq, org_user_admin, opportunity):
        wa_not_visited = WorkAreaFactory(opportunity=opportunity, status=WorkAreaStatus.NOT_VISITED)
        wa_unassigned = WorkAreaFactory(opportunity=opportunity, status=WorkAreaStatus.UNASSIGNED)
        wa_inaccessible = WorkAreaFactory(opportunity=opportunity, status=WorkAreaStatus.INACCESSIBLE)
        wa_excluded = WorkAreaFactory(opportunity=opportunity, status=WorkAreaStatus.EXCLUDED)

        res = exclude_work_areas_for_opportunity(
            opportunity=opportunity,
            work_area_ids=[wa_not_visited.id, wa_unassigned.id, wa_inaccessible.id, wa_excluded.id],
            user=org_user_admin,
            exclusion_reason="Test",
        )
        assert set(res["excluded_ids"]) == {wa_not_visited.id, wa_unassigned.id}
        assert res["skipped"] == 2
        assert res["failed"] == 0

        wa_not_visited.refresh_from_db()
        wa_unassigned.refresh_from_db()
        wa_inaccessible.refresh_from_db()
        wa_excluded.refresh_from_db()

        assert wa_not_visited.status == WorkAreaStatus.EXCLUDED
        assert wa_unassigned.status == WorkAreaStatus.EXCLUDED
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

    @patch("commcare_connect.microplanning.helpers.bulk_create_or_update_cases")
    def test_excluding_subset_recomputes_group_centroid(self, mock_bulk_hq, org_user_admin, opportunity):
        """Excluding some areas recomputes the group centroid from the areas that remain."""
        access = OpportunityAccessFactory(opportunity=opportunity)
        group = WorkAreaGroupFactory(opportunity=opportunity)
        wa_keep = WorkAreaFactory(
            opportunity=opportunity,
            opportunity_access=access,
            status=WorkAreaStatus.NOT_VISITED,
            work_area_group=group,
            boundary=_unit_square(79, 30),  # centroid (79.5, 30.5)
        )
        wa_exclude = WorkAreaFactory(
            opportunity=opportunity,
            opportunity_access=access,
            status=WorkAreaStatus.NOT_VISITED,
            work_area_group=group,
            boundary=_unit_square(77, 28),  # centroid (77.5, 28.5)
        )
        group.update_centroid()
        old_centroid = group.centroid
        assert old_centroid is not None

        res = exclude_work_areas_for_opportunity(
            opportunity=opportunity,
            work_area_ids=[wa_exclude.id],
            user=org_user_admin,
            exclusion_reason="Flooding",
        )
        assert res["excluded_ids"] == [wa_exclude.id]

        # The excluded area is dropped from the calculation, so the centroid is now wa_keep's alone.
        group.refresh_from_db()
        assert group.centroid != old_centroid
        assert group.centroid.x == 79.5
        assert group.centroid.y == 30.5
        assert wa_keep.work_area_group_id == group.id  # kept area still belongs to the group

    @patch("commcare_connect.microplanning.helpers.bulk_create_or_update_cases")
    def test_excluding_all_areas_sets_group_centroid_to_none(self, mock_bulk_hq, org_user_admin, opportunity):
        access = OpportunityAccessFactory(opportunity=opportunity)
        group = WorkAreaGroupFactory(opportunity=opportunity)
        work_areas = WorkAreaFactory.create_batch(
            2,
            opportunity=opportunity,
            opportunity_access=access,
            status=WorkAreaStatus.NOT_VISITED,
            work_area_group=group,
        )
        group.update_centroid()
        assert group.centroid is not None

        exclude_work_areas_for_opportunity(
            opportunity=opportunity,
            work_area_ids=[wa.id for wa in work_areas],
            user=org_user_admin,
            exclusion_reason="Flooding",
        )

        group.refresh_from_db()
        assert group.centroid is None

    @patch("commcare_connect.microplanning.helpers.bulk_create_or_update_cases")
    def test_exclusion_recomputes_each_affected_group_independently(self, mock_bulk_hq, org_user_admin, opportunity):
        access = OpportunityAccessFactory(opportunity=opportunity)
        group_a = WorkAreaGroupFactory(opportunity=opportunity, name="group_a")
        group_b = WorkAreaGroupFactory(opportunity=opportunity, name="group_b")

        a_keep = WorkAreaFactory(
            opportunity=opportunity,
            opportunity_access=access,
            status=WorkAreaStatus.NOT_VISITED,
            work_area_group=group_a,
            boundary=_unit_square(70, 10),  # (70.5, 10.5)
        )
        a_exclude = WorkAreaFactory(
            opportunity=opportunity,
            opportunity_access=access,
            status=WorkAreaStatus.NOT_VISITED,
            work_area_group=group_a,
            boundary=_unit_square(80, 20),
        )
        b_keep = WorkAreaFactory(
            opportunity=opportunity,
            opportunity_access=access,
            status=WorkAreaStatus.NOT_VISITED,
            work_area_group=group_b,
            boundary=_unit_square(60, 40),  # (60.5, 40.5)
        )
        b_exclude = WorkAreaFactory(
            opportunity=opportunity,
            opportunity_access=access,
            status=WorkAreaStatus.NOT_VISITED,
            work_area_group=group_b,
            boundary=_unit_square(50, 30),
        )
        group_a.update_centroid()
        group_b.update_centroid()

        exclude_work_areas_for_opportunity(
            opportunity=opportunity,
            work_area_ids=[a_exclude.id, b_exclude.id],
            user=org_user_admin,
            exclusion_reason="Flooding",
        )

        group_a.refresh_from_db()
        group_b.refresh_from_db()
        assert (group_a.centroid.x, group_a.centroid.y) == (70.5, 10.5)
        assert (group_b.centroid.x, group_b.centroid.y) == (60.5, 40.5)
        assert a_keep.work_area_group_id == group_a.id
        assert b_keep.work_area_group_id == group_b.id


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

    @patch("commcare_connect.microplanning.helpers.bulk_create_or_update_cases")
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

    @patch("commcare_connect.microplanning.helpers.bulk_create_or_update_cases")
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

    @patch("commcare_connect.microplanning.helpers.bulk_create_or_update_cases")
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

    @patch("commcare_connect.microplanning.helpers.HQ_UNASSIGN_BULK_CHUNK_SIZE", 50)
    @patch("commcare_connect.microplanning.helpers.bulk_create_or_update_cases")
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

    @patch("commcare_connect.microplanning.helpers.bulk_create_or_update_cases")
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

    @patch("commcare_connect.microplanning.helpers.HQ_UNASSIGN_BULK_CHUNK_SIZE", 50)
    @patch("commcare_connect.microplanning.helpers.bulk_create_or_update_cases")
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
