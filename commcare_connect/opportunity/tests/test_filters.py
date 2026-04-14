from datetime import date, timedelta

import pytest
from django.utils import timezone

from commcare_connect.opportunity.filters import TasksFilterSet, UserTasksFilterSet
from commcare_connect.opportunity.helpers import get_worker_tasks_base_queryset
from commcare_connect.opportunity.models import AssignedTask, AssignedTaskStatus
from commcare_connect.opportunity.tests.factories import (
    AssignedTaskFactory,
    OpportunityAccessFactory,
    OpportunityFactory,
    TaskTypeFactory,
)


@pytest.mark.django_db
def test_tasks_filterset_worker_name():
    opp = OpportunityFactory()
    access_alice = OpportunityAccessFactory(opportunity=opp, accepted=True, user__name="Alice Smith")
    access_bob = OpportunityAccessFactory(opportunity=opp, accepted=True, user__name="Bob Jones")
    task_type = TaskTypeFactory(opportunity=opp, app=opp.deliver_app, is_active=True)
    AssignedTaskFactory(opportunity_access=access_alice, task_type=task_type)
    AssignedTaskFactory(opportunity_access=access_bob, task_type=task_type)

    qs = get_worker_tasks_base_queryset(opp)
    filterset = TasksFilterSet(data={"worker_name": [str(access_alice.user.pk)]}, queryset=qs, opportunity=opp)

    assert filterset.form.is_valid()
    choices = dict(filterset.form.fields["worker_name"].choices)
    assert str(access_alice.user.pk) in choices
    assert str(access_bob.user.pk) in choices
    result = list(filterset.qs)
    assert len(result) == 1
    assert result[0].user == access_alice.user


@pytest.mark.django_db
def test_tasks_filterset_task_status_single():
    opp = OpportunityFactory()
    access = OpportunityAccessFactory(opportunity=opp, accepted=True)
    task_type = TaskTypeFactory(opportunity=opp, app=opp.deliver_app, is_active=True)
    AssignedTaskFactory(opportunity_access=access, task_type=task_type, status=AssignedTaskStatus.ASSIGNED)
    AssignedTaskFactory(opportunity_access=access, task_type=task_type, status=AssignedTaskStatus.COMPLETED)

    qs = get_worker_tasks_base_queryset(opp)
    filterset = TasksFilterSet(data={"task_status": [AssignedTaskStatus.COMPLETED]}, queryset=qs, opportunity=opp)

    assert filterset.form.is_valid()
    result = list(filterset.qs)
    assert len(result) == 1
    assert result[0].task_status == AssignedTaskStatus.COMPLETED


@pytest.mark.django_db
def test_tasks_filterset_task_type():
    opp = OpportunityFactory()
    access = OpportunityAccessFactory(opportunity=opp, accepted=True)
    task_a = TaskTypeFactory(opportunity=opp, app=opp.deliver_app, is_active=True, name="Survey")
    task_b = TaskTypeFactory(opportunity=opp, app=opp.deliver_app, is_active=True, name="Follow-up")
    AssignedTaskFactory(opportunity_access=access, task_type=task_a)
    AssignedTaskFactory(opportunity_access=access, task_type=task_b)

    qs = get_worker_tasks_base_queryset(opp)
    filterset = TasksFilterSet(data={"task_type": [str(task_a.pk)]}, queryset=qs, opportunity=opp)

    assert filterset.form.is_valid()
    choices = dict(filterset.form.fields["task_type"].choices)
    assert str(task_a.pk) in choices
    assert str(task_b.pk) in choices
    result = list(filterset.qs)
    assert len(result) == 1
    assert result[0].task_name == task_a.name


@pytest.mark.django_db
def test_tasks_filterset_task_type_excludes_inactive():
    opp = OpportunityFactory()
    active_task = TaskTypeFactory(opportunity=opp, app=opp.deliver_app, is_active=True, name="Active")
    inactive_task = TaskTypeFactory(opportunity=opp, app=opp.deliver_app, is_active=False, name="Inactive")

    qs = get_worker_tasks_base_queryset(opp)
    filterset = TasksFilterSet(data={}, queryset=qs, opportunity=opp)

    choices = dict(filterset.form.fields["task_type"].choices)
    assert str(active_task.pk) in choices
    assert str(inactive_task.pk) not in choices


@pytest.mark.django_db
class TestUserTasksFilterSet:
    @pytest.fixture(autouse=True)
    def setup(self):
        self.opp = OpportunityFactory()
        self.access = OpportunityAccessFactory(opportunity=self.opp, accepted=True)
        self.task_type = TaskTypeFactory(opportunity=self.opp, app=self.opp.deliver_app, is_active=True)

    def _filterset(self, data):
        qs = AssignedTask.objects.filter(opportunity_access__opportunity=self.opp)
        return UserTasksFilterSet(data=data, queryset=qs, opportunity=self.opp)

    def test_task_status(self):
        AssignedTaskFactory(
            opportunity_access=self.access, task_type=self.task_type, status=AssignedTaskStatus.ASSIGNED
        )
        AssignedTaskFactory(
            opportunity_access=self.access, task_type=self.task_type, status=AssignedTaskStatus.COMPLETED
        )

        filterset = self._filterset({"task_status": [AssignedTaskStatus.ASSIGNED]})

        assert filterset.form.is_valid()
        result = list(filterset.qs)
        assert len(result) == 1
        assert result[0].status == AssignedTaskStatus.ASSIGNED

    def test_task_type(self):
        task_a = TaskTypeFactory(opportunity=self.opp, app=self.opp.deliver_app, is_active=True, name="Survey")
        task_b = TaskTypeFactory(opportunity=self.opp, app=self.opp.deliver_app, is_active=True, name="Follow-up")
        inactive = TaskTypeFactory(opportunity=self.opp, app=self.opp.deliver_app, is_active=False, name="Old")
        AssignedTaskFactory(opportunity_access=self.access, task_type=task_a)
        AssignedTaskFactory(opportunity_access=self.access, task_type=task_b)

        filterset = self._filterset({"task_type": [str(task_a.pk)]})

        assert filterset.form.is_valid()
        choices = dict(filterset.form.fields["task_type"].choices)
        assert str(task_a.pk) in choices
        assert str(task_b.pk) in choices
        assert str(inactive.pk) not in choices
        result = list(filterset.qs)
        assert len(result) == 1
        assert result[0].task_type == task_a

    def test_date_assigned_range(self):
        old_task = AssignedTaskFactory(opportunity_access=self.access, task_type=self.task_type)
        recent_task = AssignedTaskFactory(opportunity_access=self.access, task_type=self.task_type)

        now = timezone.now()
        AssignedTask.objects.filter(pk=old_task.pk).update(date_created=now - timedelta(days=10))
        AssignedTask.objects.filter(pk=recent_task.pk).update(date_created=now - timedelta(days=2))

        filterset = self._filterset({"date_assigned_from": (now.date() - timedelta(days=5)).isoformat()})

        assert filterset.form.is_valid()
        result = list(filterset.qs)
        assert len(result) == 1
        assert result[0].pk == recent_task.pk

    def test_due_date_range(self):
        today = date.today()
        soon_task = AssignedTaskFactory(
            opportunity_access=self.access, task_type=self.task_type, due_date=today + timedelta(days=3)
        )
        AssignedTaskFactory(
            opportunity_access=self.access, task_type=self.task_type, due_date=today + timedelta(days=30)
        )

        filterset = self._filterset({"due_date_to": (today + timedelta(days=7)).isoformat()})

        assert filterset.form.is_valid()
        result = list(filterset.qs)
        assert len(result) == 1
        assert result[0].pk == soon_task.pk
