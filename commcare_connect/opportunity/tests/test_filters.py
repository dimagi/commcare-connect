from datetime import date, timedelta

import pytest
from waffle.testutils import override_switch

from commcare_connect.flags.switch_names import USER_VISIT_FILTERS
from commcare_connect.opportunity.filters import AssignedTaskFilterSet, TasksFilterSet, UserVisitFilterSet
from commcare_connect.opportunity.helpers import get_worker_tasks_base_queryset
from commcare_connect.opportunity.models import AssignedTask, AssignedTaskStatus, UserVisit
from commcare_connect.opportunity.tests.factories import (
    AssignedTaskFactory,
    CompletedWorkFactory,
    DeliverUnitFactory,
    OpportunityAccessFactory,
    OpportunityFactory,
    OpportunityVerificationFlagsFactory,
    PaymentUnitFactory,
    TaskFactory,
    UserVisitFactory,
)
from commcare_connect.utils.flags import Flags


@pytest.mark.django_db
@override_switch(USER_VISIT_FILTERS, active=True)
def test_uservisit_filterset_filters_by_flags():
    opportunity = OpportunityFactory()
    OpportunityVerificationFlagsFactory(
        opportunity=opportunity,
        duplicate=True,
        gps=False,
        location=0,
        catchment_areas=False,
    )

    access = OpportunityAccessFactory(opportunity=opportunity)
    payment_unit = PaymentUnitFactory(opportunity=opportunity)
    deliver_unit = DeliverUnitFactory(app=opportunity.deliver_app, payment_unit=payment_unit)
    completed_work_flagged = CompletedWorkFactory(opportunity_access=access, payment_unit=payment_unit)
    completed_work_clean = CompletedWorkFactory(opportunity_access=access, payment_unit=payment_unit)

    flagged_visit = UserVisitFactory(
        opportunity=opportunity,
        opportunity_access=access,
        user=access.user,
        deliver_unit=deliver_unit,
        completed_work=completed_work_flagged,
        flagged=True,
        flag_reason={"flags": [(Flags.DUPLICATE.value, "Duplicate submission")]},
    )
    clean_visit = UserVisitFactory(
        opportunity=opportunity,
        opportunity_access=access,
        user=access.user,
        deliver_unit=deliver_unit,
        completed_work=completed_work_clean,
        flagged=False,
        flag_reason=None,
    )

    filterset = UserVisitFilterSet(
        data={"flags": [Flags.DUPLICATE.value]},
        queryset=UserVisit.objects.filter(opportunity=opportunity),
        opportunity=opportunity,
    )

    assert "flags" in filterset.filters
    available_flags = dict(filterset.filters["flags"].extra["choices"])
    assert Flags.DUPLICATE.value in available_flags

    filtered_visits = set(filterset.qs.values_list("id", flat=True))
    assert flagged_visit.id in filtered_visits
    assert clean_visit.id not in filtered_visits


@pytest.mark.django_db
@override_switch(USER_VISIT_FILTERS, active=False)
def test_uservisit_filterset_user_only_when_switch_disabled():
    opportunity = OpportunityFactory()
    OpportunityAccessFactory(opportunity=opportunity)

    filterset = UserVisitFilterSet(
        data={},
        queryset=UserVisit.objects.filter(opportunity=opportunity),
        opportunity=opportunity,
    )

    assert list(filterset.filters.keys()) == ["user"]


@pytest.mark.django_db
@override_switch(USER_VISIT_FILTERS, active=False)
def test_uservisit_filterset_filters_by_user_id():
    opp = OpportunityFactory()
    access_1 = OpportunityAccessFactory(opportunity=opp)
    access_2 = OpportunityAccessFactory(opportunity=opp)

    visit_1 = UserVisitFactory(opportunity=opp, opportunity_access=access_1, user=access_1.user)
    visit_2 = UserVisitFactory(opportunity=opp, opportunity_access=access_2, user=access_2.user)

    filterset = UserVisitFilterSet(
        data={"user": str(access_1.user.user_id)},
        queryset=UserVisit.objects.filter(opportunity=opp),
        opportunity=opp,
    )

    filtered_visits = set(filterset.qs.values_list("id", flat=True))
    assert visit_1.id in filtered_visits
    assert visit_2.id not in filtered_visits


@pytest.mark.django_db
def test_tasks_filterset_worker_name():
    opp = OpportunityFactory()
    access_alice = OpportunityAccessFactory(opportunity=opp, accepted=True, user__name="Alice Smith")
    access_bob = OpportunityAccessFactory(opportunity=opp, accepted=True, user__name="Bob Jones")
    task = TaskFactory(opportunity=opp, app=opp.deliver_app, is_active=True)
    AssignedTaskFactory(opportunity_access=access_alice, task=task)
    AssignedTaskFactory(opportunity_access=access_bob, task=task)

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
    task = TaskFactory(opportunity=opp, app=opp.deliver_app, is_active=True)
    AssignedTaskFactory(opportunity_access=access, task=task, status=AssignedTaskStatus.ASSIGNED)
    AssignedTaskFactory(opportunity_access=access, task=task, status=AssignedTaskStatus.COMPLETED)

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
    task_a = TaskFactory(opportunity=opp, app=opp.deliver_app, is_active=True, name="Survey")
    task_b = TaskFactory(opportunity=opp, app=opp.deliver_app, is_active=True, name="Follow-up")
    AssignedTaskFactory(opportunity_access=access, task=task_a)
    AssignedTaskFactory(opportunity_access=access, task=task_b)

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
    active_task = TaskFactory(opportunity=opp, app=opp.deliver_app, is_active=True, name="Active")
    inactive_task = TaskFactory(opportunity=opp, app=opp.deliver_app, is_active=False, name="Inactive")

    qs = get_worker_tasks_base_queryset(opp)
    filterset = TasksFilterSet(data={}, queryset=qs, opportunity=opp)

    choices = dict(filterset.form.fields["task_type"].choices)
    assert str(active_task.pk) in choices
    assert str(inactive_task.pk) not in choices


@pytest.mark.django_db
class TestAssignedTaskFilterSet:
    def setup_method(self):
        self.opportunity = OpportunityFactory()
        self.access1, self.access2 = OpportunityAccessFactory.create_batch(2, opportunity=self.opportunity)
        self.task1, self.task2 = TaskFactory.create_batch(2, opportunity=self.opportunity)
        self.at_assigned = AssignedTaskFactory(
            task=self.task1,
            opportunity_access=self.access1,
            status=AssignedTaskStatus.ASSIGNED,
        )
        self.at_completed = AssignedTaskFactory(
            task=self.task2,
            opportunity_access=self.access2,
            status=AssignedTaskStatus.COMPLETED,
        )

    def filter_assigned_tasks(self, params):
        return AssignedTaskFilterSet(
            params,
            queryset=AssignedTask.objects.filter(opportunity_access__opportunity=self.opportunity),
            opportunity=self.opportunity,
        ).qs

    def test_no_filters_returns_all(self):
        assert self.filter_assigned_tasks({}).count() == 2

    def test_filter_by_worker_name(self):
        result = self.filter_assigned_tasks({"worker_name": str(self.access1.user.pk)})
        assert list(result) == [self.at_assigned]

    @pytest.mark.parametrize(
        "status,expected",
        [
            (AssignedTaskStatus.ASSIGNED, "at_assigned"),
            (AssignedTaskStatus.COMPLETED, "at_completed"),
        ],
    )
    def test_filter_by_task_status(self, status, expected):
        result = self.filter_assigned_tasks({"task_status": status})
        assert list(result) == [getattr(self, expected)]

    def test_filter_by_task_type(self):
        result = self.filter_assigned_tasks({"task_type": str(self.task1.pk)})
        assert list(result) == [self.at_assigned]

    def test_filter_by_date_assigned_range(self):
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        result = self.filter_assigned_tasks({"date_assigned_after": yesterday, "date_assigned_before": tomorrow})
        assert result.count() == 2

    def test_filter_by_date_assigned_excludes_outside_range(self):
        future = (date.today() + timedelta(days=10)).isoformat()
        result = self.filter_assigned_tasks({"date_assigned_after": future})
        assert result.count() == 0

    def test_filter_by_due_date_range(self):
        soon = (date.today() + timedelta(days=14)).isoformat()
        result = self.filter_assigned_tasks({"due_date_before": soon})
        assert result.count() == 2

    def test_combined_filters(self):
        result = self.filter_assigned_tasks(
            {
                "worker_name": str(self.access1.user.pk),
                "task_status": AssignedTaskStatus.ASSIGNED,
            }
        )
        assert list(result) == [self.at_assigned]
