from datetime import date, datetime, timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from commcare_connect.opportunity.filters import (
    AssignedTaskFilterSet,
    OpportunityListFilterSet,
    TasksFilterSet,
    UserTasksFilterSet,
)
from commcare_connect.opportunity.helpers import get_worker_tasks_base_queryset
from commcare_connect.opportunity.models import AssignedTask, AssignedTaskStatus, Opportunity
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
    task_a = TaskTypeFactory(app=opp.deliver_app, opportunity=opp, is_active=True, name="Survey")
    task_b = TaskTypeFactory(app=opp.deliver_app, opportunity=opp, is_active=True, name="Follow-up")
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
    active_task = TaskTypeFactory(app=opp.deliver_app, opportunity=opp, is_active=True, name="Active")
    inactive_task = TaskTypeFactory(app=opp.deliver_app, opportunity=opp, is_active=False, name="Inactive")

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
        self.task_type = TaskTypeFactory(app=self.opp.deliver_app, opportunity=self.opp, is_active=True)

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
        task_a = TaskTypeFactory(app=self.opp.deliver_app, opportunity=self.opp, is_active=True, name="Survey")
        task_b = TaskTypeFactory(app=self.opp.deliver_app, opportunity=self.opp, is_active=True, name="Follow-up")
        inactive = TaskTypeFactory(app=self.opp.deliver_app, opportunity=self.opp, is_active=False, name="Old")
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
        recent_task = AssignedTaskFactory(opportunity_access=self.access)

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
        AssignedTaskFactory(opportunity_access=self.access, due_date=today + timedelta(days=30))

        filterset = self._filterset({"due_date_to": (today + timedelta(days=7)).isoformat()})

        assert filterset.form.is_valid()
        result = list(filterset.qs)
        assert len(result) == 1
        assert result[0].pk == soon_task.pk


@pytest.mark.django_db
class TestAssignedTaskFilterSet:
    def setup_method(self):
        self.opportunity = OpportunityFactory()
        self.access1, self.access2 = OpportunityAccessFactory.create_batch(2, opportunity=self.opportunity)
        self.task1, self.task2 = TaskTypeFactory.create_batch(2, opportunity=self.opportunity)
        self.at_assigned = AssignedTaskFactory(
            task_type=self.task1,
            opportunity_access=self.access1,
            status=AssignedTaskStatus.ASSIGNED,
        )
        self.at_completed = AssignedTaskFactory(
            task_type=self.task2,
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

    @pytest.mark.parametrize(
        "date_filter,expected_count",
        [
            (
                {
                    "date_assigned_after": (date.today() - timedelta(days=1)).isoformat(),
                    "date_assigned_before": (date.today() + timedelta(days=1)).isoformat(),
                },
                2,
            ),
            ({"due_date_after": (date.today() + timedelta(days=10)).isoformat()}, 0),
            ({"due_date_before": (date.today() + timedelta(days=14)).isoformat()}, 2),
        ],
        ids=["date_assigned_range_matches_all", "due_date_after_excludes_all", "due_date_before_matches_all"],
    )
    def test_filter_by_date_assigned_and_due_date(self, date_filter, expected_count):
        result = self.filter_assigned_tasks(date_filter)
        assert result.count() == expected_count

    def test_combined_filters(self):
        result = self.filter_assigned_tasks(
            {
                "worker_name": str(self.access1.user.pk),
                "task_status": AssignedTaskStatus.ASSIGNED,
            }
        )
        assert list(result) == [self.at_assigned]


def _opportunity_list_filters(org, rf):
    request = rf.get("/")
    request.org = org
    return OpportunityListFilterSet(queryset=Opportunity.objects.none(), request=request).filters


@pytest.mark.django_db
class TestOpportunityListProgramFilter:
    """The Program dropdown on the opportunity list is built from the programs the org can reach."""

    def test_every_accessible_program_is_offered(self, program, rf):
        filters = _opportunity_list_filters(program.organization, rf)

        assert filters["program"].extra["choices"] == [(program.slug, program.name)]

    def test_the_filter_is_dropped_without_an_accessible_program(self, organization, rf):
        assert "program" not in _opportunity_list_filters(organization, rf)


@pytest.mark.django_db
class TestOpportunityListDeliveryTypeFilter:
    """The Delivery Type dropdown offers only the delivery types of opportunities the org can reach."""

    def test_only_delivery_types_in_use_are_offered(self, organization, rf):
        used = OpportunityFactory(organization=organization).delivery_type
        OpportunityFactory()  # another org's opportunity and delivery type

        assert _opportunity_list_filters(organization, rf)["delivery_type"].extra["choices"] == [
            (str(used.pk), used.name)
        ]

    def test_archived_opportunities_delivery_types_are_not_offered(self, organization, rf):
        live = OpportunityFactory(organization=organization).delivery_type
        OpportunityFactory(organization=organization, archived=True)

        assert _opportunity_list_filters(organization, rf)["delivery_type"].extra["choices"] == [
            (str(live.pk), live.name)
        ]

    def test_the_filter_is_dropped_without_an_accessible_opportunity(self, organization, rf):
        assert "delivery_type" not in _opportunity_list_filters(organization, rf)


class TestOpportunityListDateRanges:
    """Each date's range dropdown is resolved into the from/to dates the opportunity list filters on."""

    TODAY = date(2026, 5, 31)

    def cleaned(self, data):
        filterset = OpportunityListFilterSet(data=data, queryset=Opportunity.objects.none())
        with patch("commcare_connect.opportunity.filters.now", return_value=datetime(2026, 5, 31, 12)):
            assert filterset.form.is_valid(), filterset.form.errors
        return filterset.form.cleaned_data

    @pytest.mark.parametrize(
        "field, preset, expected",
        [
            ("start_date", "last_30_days", (date(2026, 5, 1), TODAY)),
            ("start_date", "last_3_months", (date(2026, 2, 28), TODAY)),
            ("start_date", "last_12_months", (date(2025, 5, 31), TODAY)),
            ("end_date", "last_6_months", (date(2025, 11, 30), TODAY)),
            ("end_date", "next_30_days", (TODAY, date(2026, 6, 30))),
            ("end_date", "next_3_months", (TODAY, date(2026, 8, 31))),
        ],
    )
    def test_a_preset_sets_its_dates_and_ignores_custom_ones(self, field, preset, expected):
        cleaned = self.cleaned({f"{field}_range": preset, f"{field}_from": "2020-01-01", f"{field}_to": "2020-12-31"})

        assert (cleaned[f"{field}_from"], cleaned[f"{field}_to"]) == expected

    def test_future_presets_are_offered_for_end_dates_only(self):
        filterset = OpportunityListFilterSet(queryset=Opportunity.objects.none())

        start_choices = dict(filterset.form.fields["start_date_range"].choices)
        end_choices = dict(filterset.form.fields["end_date_range"].choices)
        assert "next_30_days" not in start_choices
        assert "next_30_days" in end_choices

    @pytest.mark.parametrize(
        "data, expected",
        [
            ({"start_date_range": "custom", "start_date_from": "2026-01-01"}, ("custom", date(2026, 1, 1), None)),
            ({"start_date_range": "custom"}, ("", None, None)),
            ({"start_date_from": "2026-01-01", "start_date_to": "2026-02-01"}, ("", None, None)),
        ],
    )
    def test_custom_dates_apply_only_under_custom_range(self, data, expected):
        cleaned = self.cleaned(data)

        assert (cleaned["start_date_range"], cleaned["start_date_from"], cleaned["start_date_to"]) == expected
