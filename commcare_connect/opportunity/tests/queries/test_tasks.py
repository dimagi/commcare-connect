import pytest
from django.utils.timezone import now

from commcare_connect.opportunity.models import AssignedTaskStatus, TaskType, UserInviteStatus
from commcare_connect.opportunity.queries.tasks import (
    get_assignable_task_types,
    get_assigned_task_counts,
    get_worker_tasks_base_queryset,
    has_blocking_pending_task,
)
from commcare_connect.opportunity.tests.factories import (
    AssignedTaskFactory,
    OpportunityAccessFactory,
    TaskTypeFactory,
    UserInviteFactory,
)


@pytest.mark.django_db
def test_get_worker_tasks_base_queryset(opportunity):
    access = OpportunityAccessFactory(opportunity=opportunity, accepted=True)
    UserInviteFactory(opportunity=opportunity, opportunity_access=access, status="accepted")
    AssignedTaskFactory(opportunity_access=access)
    AssignedTaskFactory(opportunity_access=access)

    result = list(get_worker_tasks_base_queryset(opportunity))
    assert len(result) == 2
    assert result[0].user is not None
    assert result[0].task_name is not None
    assert result[0].status == UserInviteStatus.accepted


@pytest.mark.django_db
def test_get_assigned_task_counts(opportunity):
    access = OpportunityAccessFactory(opportunity=opportunity)
    AssignedTaskFactory.create_batch(2, opportunity_access=access)
    AssignedTaskFactory(opportunity_access=access, status=AssignedTaskStatus.COMPLETED)
    AssignedTaskFactory()  # another opportunity

    assert get_assigned_task_counts(opportunity) == {"total_tasks": 3, "open_tasks": 2, "complete_tasks": 1}


@pytest.mark.django_db
class TestGetAssignableTaskTypes:
    @pytest.fixture
    def access(self, opportunity):
        return OpportunityAccessFactory(opportunity=opportunity)

    def test_lists_active_task_types_of_deliver_app(self, opportunity):
        active = TaskTypeFactory(app=opportunity.deliver_app)
        TaskTypeFactory(app=opportunity.deliver_app, is_active=False)
        TaskTypeFactory()  # another app

        assert list(get_assignable_task_types(opportunity=opportunity)) == [active]

    @pytest.mark.parametrize(
        "status, assignable", [(AssignedTaskStatus.ASSIGNED, False), (AssignedTaskStatus.COMPLETED, True)]
    )
    def test_excludes_task_types_open_for_access(self, opportunity, access, status, assignable):
        task_type = TaskTypeFactory(app=opportunity.deliver_app)
        AssignedTaskFactory(opportunity_access=access, task_type=task_type, status=status)

        result = get_assignable_task_types(opportunity=opportunity, access=access)

        assert (task_type in result) is assignable


@pytest.mark.django_db
class TestHasBlockingPendingTask:
    @pytest.fixture
    def access(self, opportunity):
        return OpportunityAccessFactory(opportunity=opportunity)

    @pytest.mark.parametrize(
        "task_kwargs, blocks",
        [
            ({}, True),
            ({"status": AssignedTaskStatus.COMPLETED}, False),
            ({"task_type__is_active": False}, False),
            ({"task_type__archived": now()}, False),
        ],
        ids=["open", "completed", "inactive_type", "archived_type"],
    )
    def test_open_task_of_live_type_blocks(self, opportunity, access, task_kwargs, blocks):
        AssignedTaskFactory(opportunity_access=access, task_type__app=opportunity.deliver_app, **task_kwargs)

        assert has_blocking_pending_task(access=access, app=opportunity.deliver_app) is blocks

    def test_task_on_other_app_does_not_block(self, opportunity, access):
        AssignedTaskFactory(opportunity_access=access)

        assert has_blocking_pending_task(access=access, app=opportunity.deliver_app) is False


@pytest.mark.django_db
def test_task_type_active_for(opportunity):
    active = TaskTypeFactory(opportunity=opportunity)
    TaskTypeFactory(opportunity=opportunity, is_active=False)
    TaskTypeFactory()  # another opportunity

    assert list(TaskType.objects.active_for(opportunity)) == [active]
