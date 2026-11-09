from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.core.exceptions import ValidationError

from commcare_connect.microplanning.models import WorkAreaGroup
from commcare_connect.microplanning.services.work_area_groups import (
    clear_work_area_groups_for_opportunity,
    get_cluster_area_cache_lock_key,
    start_work_area_clustering,
)
from commcare_connect.microplanning.tests.factories import WorkAreaFactory, WorkAreaGroupFactory
from commcare_connect.opportunity.tests.factories import OpportunityAccessFactory


@pytest.mark.django_db
class TestClearWorkAreaGroupsForOpportunity:
    def test_deletes_only_this_opportunitys_groups(self, opportunity):
        WorkAreaGroupFactory(opportunity=opportunity)
        other_group = WorkAreaGroupFactory()

        clear_work_area_groups_for_opportunity(opportunity)

        assert list(WorkAreaGroup.objects.all()) == [other_group]

    def test_refuses_while_work_areas_are_assigned(self, opportunity):
        WorkAreaGroupFactory(opportunity=opportunity)
        WorkAreaFactory(opportunity=opportunity, opportunity_access=OpportunityAccessFactory(opportunity=opportunity))

        with pytest.raises(ValidationError):
            clear_work_area_groups_for_opportunity(opportunity)

        assert WorkAreaGroup.objects.filter(opportunity=opportunity).exists()


@pytest.mark.django_db
class TestStartWorkAreaClustering:
    @patch("commcare_connect.microplanning.tasks.cluster_work_areas_task.apply_async")
    def test_queues_clustering_after_commit(self, apply_async, opportunity, django_capture_on_commit_callbacks):
        WorkAreaFactory(opportunity=opportunity)

        with django_capture_on_commit_callbacks() as callbacks:
            task_id = start_work_area_clustering(opportunity=opportunity, max_buildings=150)
        apply_async.assert_not_called()
        for callback in callbacks:
            callback()

        apply_async.assert_called_once_with(args=[opportunity.id, 150], task_id=task_id)

    @pytest.mark.parametrize(
        ("has_work_areas", "has_groups", "message"),
        [
            (False, False, "Please upload Work Areas"),
            (True, True, "Work Area Groups already exist"),
        ],
    )
    @patch("commcare_connect.microplanning.tasks.cluster_work_areas_task.apply_async")
    def test_refuses_without_work_areas_or_with_groups(
        self, apply_async, opportunity, has_work_areas, has_groups, message
    ):
        if has_work_areas:
            WorkAreaFactory(opportunity=opportunity)
        if has_groups:
            WorkAreaGroupFactory(opportunity=opportunity)

        with pytest.raises(ValidationError, match=message):
            start_work_area_clustering(opportunity=opportunity, max_buildings=150)
        apply_async.assert_not_called()

    @patch("commcare_connect.microplanning.tasks.cluster_work_areas_task.apply_async")
    def test_refuses_while_clustering_runs(self, apply_async, opportunity):
        WorkAreaFactory(opportunity=opportunity)

        with cache.lock(get_cluster_area_cache_lock_key(opportunity.id), timeout=5):
            with pytest.raises(ValidationError, match="already in progress"):
                start_work_area_clustering(opportunity=opportunity, max_buildings=150)
        apply_async.assert_not_called()
