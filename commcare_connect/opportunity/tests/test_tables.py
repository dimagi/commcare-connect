import pytest
from django.test import RequestFactory
from django_tables2 import RequestConfig

from commcare_connect.opportunity.helpers import get_worker_tasks_base_queryset
from commcare_connect.opportunity.models import AssignedTaskStatus
from commcare_connect.opportunity.tables import WorkerTasksTable
from commcare_connect.opportunity.tests.factories import (
    AssignedTaskFactory,
    OpportunityAccessFactory,
    TaskTypeFactory,
    UserInviteFactory,
)


def _make_table(opportunity, per_page=25):
    data = get_worker_tasks_base_queryset(opportunity)
    table = WorkerTasksTable(data, org_slug="test-org", opp_id=opportunity.opportunity_id)
    rf = RequestFactory()
    request = rf.get("/")
    RequestConfig(request, paginate={"per_page": per_page}).configure(table)
    return table


@pytest.mark.django_db
def test_worker_tasks_table_groups_by_user(opportunity):
    access1 = OpportunityAccessFactory(opportunity=opportunity, accepted=True, user__name="Alice")
    UserInviteFactory(opportunity=opportunity, opportunity_access=access1, status="accepted")
    access2 = OpportunityAccessFactory(opportunity=opportunity, accepted=True, user__name="Bob")
    UserInviteFactory(opportunity=opportunity, opportunity_access=access2, status="invited")

    task_type = TaskTypeFactory(opportunity=opportunity, app=opportunity.deliver_app, is_active=True)
    AssignedTaskFactory(opportunity_access=access1, task_type=task_type, status=AssignedTaskStatus.ASSIGNED)
    AssignedTaskFactory(opportunity_access=access1, task_type=task_type, status=AssignedTaskStatus.COMPLETED)
    AssignedTaskFactory(opportunity_access=access2, task_type=task_type, status=AssignedTaskStatus.ASSIGNED)

    data = get_worker_tasks_base_queryset(opportunity)
    rows = list(data)
    assert len(rows) == 3

    # Alice's 2 tasks should come first (sorted by name), grouped together
    assert rows[0].pk == rows[1].pk == access1.pk
    assert rows[2].pk == access2.pk


@pytest.mark.django_db
def test_worker_tasks_table_empty(opportunity):
    table = _make_table(opportunity)
    rows = list(table.rows)
    assert len(rows) == 0
