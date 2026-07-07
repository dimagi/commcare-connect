import datetime
from datetime import date, timedelta
from unittest import mock

import pytest
from django.db.utils import IntegrityError
from django.utils.timezone import now

from commcare_connect.opportunity.exceptions import TaskAlreadyAssignedError
from commcare_connect.opportunity.models import (
    AssignedTask,
    AssignedTaskStatus,
    AudioAttachment,
    InvoiceStatus,
    Opportunity,
    OpportunityActiveEvent,  # added via pghistory
    OpportunityClaimLimit,
    PaymentInvoice,
    PaymentInvoiceStatusEvent,  # added via pghistory
    TaskTypeModeChoices,
)
from commcare_connect.opportunity.tests.factories import (
    AssignedTaskFactory,
    CompletedModuleFactory,
    CompletedWorkFactory,
    DeliverUnitFactory,
    LearnModuleFactory,
    OpportunityAccessFactory,
    OpportunityClaimFactory,
    OpportunityClaimLimitFactory,
    OpportunityFactory,
    PaymentInvoiceFactory,
    PaymentUnitFactory,
    TaskTypeFactory,
    UserVisitFactory,
)
from commcare_connect.opportunity.utils.invoice import generate_invoice_number
from commcare_connect.opportunity.visit_import import update_payment_accrued
from commcare_connect.users.models import User
from commcare_connect.users.tests.factories import MobileUserFactory
from commcare_connect.utils.commcarehq_api import CommCareHQAPIException
from commcare_connect.utils.ocs_api import OcsApiError


@pytest.mark.django_db
class TestPaymentInvoice:
    def test_pghistory_tracking(self):
        payment_invoice = PaymentInvoiceFactory()

        invoice_status_events = payment_invoice.status_events.all()
        assert len(invoice_status_events) == 1
        assert invoice_status_events[0].status == InvoiceStatus.PENDING_NM_REVIEW
        # no context since this was not done via django view/request but directly via model
        assert invoice_status_events[0].pgh_context is None

        payment_invoice.status = InvoiceStatus.PENDING_PM_REVIEW
        payment_invoice.save()

        assert payment_invoice.status_events.count() == 2
        recent_invoice_status_event = payment_invoice.status_events.last()
        assert recent_invoice_status_event.status == InvoiceStatus.PENDING_PM_REVIEW
        # no context since this was not done via django view/request but directly via model
        assert recent_invoice_status_event.pgh_context is None

    def test_pghistory_tracking_for_bulk_actions(self):
        opportunity = OpportunityFactory()
        payment_invoices = []

        assert PaymentInvoiceStatusEvent.objects.count() == 0
        assert PaymentInvoice.objects.count() == 0

        for counter in range(1, 11):
            payment_invoices.append(
                PaymentInvoice(
                    opportunity=opportunity,
                    amount=10,
                    date=datetime.date.today(),
                    invoice_number=generate_invoice_number(),
                )
            )

        # bulk create action
        created_invoices = PaymentInvoice.objects.bulk_create(payment_invoices)
        created_invoice_ids = {invoice.pk for invoice in created_invoices}

        # assert events created for each created record
        assert len(created_invoices) == 10
        assert PaymentInvoiceStatusEvent.objects.count() == 10
        assert PaymentInvoiceStatusEvent.objects.filter(status=InvoiceStatus.PENDING_NM_REVIEW).count() == 10

        create_invoice_events_invoice_obj_ids = {
            invoice_event.pgh_obj_id for invoice_event in PaymentInvoiceStatusEvent.objects.all()
        }
        assert created_invoice_ids == create_invoice_events_invoice_obj_ids

        # bulk update action
        updated_invoices_count = PaymentInvoice.objects.filter(pk__in=created_invoice_ids).update(
            status=InvoiceStatus.PENDING_PM_REVIEW
        )

        # assert successful update
        assert updated_invoices_count == 10
        assert PaymentInvoice.objects.filter(status=InvoiceStatus.PENDING_NM_REVIEW).count() == 0
        assert PaymentInvoice.objects.filter(status=InvoiceStatus.PENDING_PM_REVIEW).count() == 10

        # assert new events created for each updated record
        assert PaymentInvoiceStatusEvent.objects.count() == 20
        assert PaymentInvoiceStatusEvent.objects.filter(status=InvoiceStatus.PENDING_NM_REVIEW).count() == 10
        assert PaymentInvoiceStatusEvent.objects.filter(status=InvoiceStatus.PENDING_PM_REVIEW).count() == 10

        # assert expected status value for the recent event for each record
        for invoice in created_invoices:
            assert invoice.status_events.last().status == InvoiceStatus.PENDING_PM_REVIEW


@pytest.mark.django_db
def test_learn_progress(opportunity: Opportunity):
    learn_modules = LearnModuleFactory.create_batch(2, app=opportunity.learn_app)
    access_1, access_2 = OpportunityAccessFactory.create_batch(2, opportunity=opportunity)
    for learn_module in learn_modules:
        CompletedModuleFactory(module=learn_module, opportunity_access=access_1)
    assert access_1.learn_progress == 100
    assert access_2.learn_progress == 0


@pytest.mark.django_db
@pytest.mark.parametrize("opportunity", [{}, {"opp_options": {"managed": True}}], indirect=True)
def test_opportunity_stats(opportunity: Opportunity, user: User):
    payment_unit_sub = PaymentUnitFactory.create(
        opportunity=opportunity, max_total=100, max_daily=10, amount=5, parent_payment_unit=None
    )
    payment_unit1 = PaymentUnitFactory.create(
        opportunity=opportunity,
        max_total=100,
        max_daily=10,
        amount=3,
        parent_payment_unit=payment_unit_sub,
    )
    payment_unit2 = PaymentUnitFactory.create(
        opportunity=opportunity, max_total=100, max_daily=10, amount=5, parent_payment_unit=None
    )
    assert set(list(opportunity.paymentunit_set.values_list("id", flat=True))) == {
        payment_unit1.id,
        payment_unit2.id,
        payment_unit_sub.id,
    }
    payment_units = [payment_unit_sub, payment_unit1, payment_unit2]
    budget_per_user = sum(pu.max_total * (pu.amount + pu.org_amount) for pu in payment_units)
    opportunity.total_budget = budget_per_user * 3

    payment_units = [payment_unit1, payment_unit2, payment_unit_sub]
    assert opportunity.budget_per_user == sum([p.amount * p.max_total for p in payment_units])
    assert opportunity.number_of_users == 3
    assert opportunity.allotted_visits == sum([pu.max_total for pu in payment_units]) * opportunity.number_of_users
    assert opportunity.max_visits_per_user == sum([pu.max_total for pu in payment_units])
    assert opportunity.daily_max_visits_per_user == sum([pu.max_daily for pu in payment_units])
    assert opportunity.budget_per_visit == sum([pu.amount for pu in payment_units])

    access = OpportunityAccessFactory(user=user, opportunity=opportunity)
    claim = OpportunityClaimFactory(opportunity_access=access)

    ocl1 = OpportunityClaimLimitFactory(opportunity_claim=claim, payment_unit=payment_unit1)
    ocl2 = OpportunityClaimLimitFactory(opportunity_claim=claim, payment_unit=payment_unit2)

    assert opportunity.claimed_budget == (ocl1.max_visits * (payment_unit1.amount + payment_unit1.org_amount)) + (
        ocl2.max_visits * (payment_unit2.amount + payment_unit2.org_amount)
    )
    assert opportunity.remaining_budget == opportunity.total_budget - opportunity.claimed_budget


@pytest.mark.django_db
def test_claim_limits(opportunity: Opportunity):
    payment_unit_sub = PaymentUnitFactory(opportunity=opportunity, parent_payment_unit=None, org_amount=0)
    payment_units = PaymentUnitFactory.create_batch(
        2, opportunity=opportunity, parent_payment_unit=None, org_amount=0
    ) + [payment_unit_sub]
    payment_unit_sub.parent_payment_unit = payment_units[0]
    budget_per_user = sum([p.max_total * p.amount for p in payment_units])
    # budget not enough for more than 2 users
    opportunity.total_budget = budget_per_user * 1.5
    mobile_users = MobileUserFactory.create_batch(3)
    for mobile_user in mobile_users:
        access = OpportunityAccessFactory(user=mobile_user, opportunity=opportunity, accepted=True)
        claim = OpportunityClaimFactory(opportunity_access=access)
        OpportunityClaimLimit.create_claim_limits(opportunity, claim)

    assert opportunity.claimed_budget <= int(opportunity.total_budget)
    assert opportunity.claimed_visits <= int(opportunity.allotted_visits)
    assert opportunity.remaining_budget < payment_units[0].amount + payment_units[1].amount

    def limit_count(user):
        return OpportunityClaimLimit.objects.filter(opportunity_claim__opportunity_access__user=user).count()

    # enough for 1st user
    assert limit_count(mobile_users[0]) == 3
    # partially enough for 2nd user, depending on paymentunit.amount
    assert limit_count(mobile_users[1]) in [2, 3]
    # Not enough for 3rd user at all
    assert limit_count(mobile_users[2]) == 0


@pytest.mark.django_db
def test_access_visit_count(opportunity: Opportunity):
    access = OpportunityAccessFactory(opportunity=opportunity)
    assert access.visit_count == 0

    payment_unit = PaymentUnitFactory(opportunity=opportunity)
    deliver_unit = DeliverUnitFactory(app=opportunity.deliver_app, payment_unit=payment_unit)
    completed_work = CompletedWorkFactory(payment_unit=payment_unit, opportunity_access=access)
    UserVisitFactory(
        completed_work=completed_work, deliver_unit=deliver_unit, user=access.user, opportunity=access.opportunity
    )
    update_payment_accrued(opportunity, [access.user])
    assert access.visit_count == 1


@pytest.mark.django_db
class TestOpportunityActiveTracking:
    def test_pghistory_records_manual_deactivation(self):
        opp = OpportunityFactory()
        opp.active = False
        opp.save()
        events = OpportunityActiveEvent.objects.filter(pgh_obj=opp).order_by("pgh_id")
        assert events.count() == 1
        assert events.last().active is False
        # No request context in tests — both events should have no context
        assert events.first().pgh_context is None
        assert events.last().pgh_context is None


@pytest.mark.django_db
class TestAssignedTaskAssign:
    def test_creates_row_and_pushes_to_hq(self):
        access = OpportunityAccessFactory()
        task_type = TaskTypeFactory(app=access.opportunity.deliver_app, case_property="needs_assessment")
        due_date = date.today() + timedelta(days=7)
        assigner = access.user

        with mock.patch("commcare_connect.commcarehq.api.bulk_update_usercases") as mock_update:
            assigned = AssignedTask.assign(
                task_type=task_type,
                opportunity_access=access,
                due_date=due_date,
                assigned_by=assigner,
            )

        mock_update.assert_called_once_with({access: {"properties": {"needs_assessment": "1"}}})
        assert isinstance(assigned, AssignedTask)
        assert assigned.task_type == task_type
        assert assigned.opportunity_access == access
        assert assigned.due_date == due_date
        assert assigned.status == AssignedTaskStatus.ASSIGNED
        assert assigned.assigned_by == assigner

    @pytest.mark.parametrize("case_property", [None, ""])
    def test_skips_hq_when_case_property_missing(self, case_property):
        access = OpportunityAccessFactory()
        task_type = TaskTypeFactory(app=access.opportunity.deliver_app, case_property=case_property)
        due_date = date.today() + timedelta(days=7)

        with mock.patch("commcare_connect.commcarehq.api.bulk_update_usercases") as mock_update:
            assigned = AssignedTask.assign(
                task_type=task_type,
                opportunity_access=access,
                due_date=due_date,
            )

        mock_update.assert_not_called()
        assert AssignedTask.objects.filter(pk=assigned.pk).exists()

    def test_raises_when_task_type_already_assigned(self):
        access = OpportunityAccessFactory()
        task_type = TaskTypeFactory(app=access.opportunity.deliver_app)
        due_date = date.today() + timedelta(days=7)
        AssignedTaskFactory(task_type=task_type, opportunity_access=access, status=AssignedTaskStatus.ASSIGNED)

        with pytest.raises(TaskAlreadyAssignedError):
            AssignedTask.assign(task_type=task_type, opportunity_access=access, due_date=due_date)

        assert AssignedTask.objects.filter(task_type=task_type, opportunity_access=access).count() == 1

    def test_allows_reassign_after_completion(self):
        access = OpportunityAccessFactory()
        task_type = TaskTypeFactory(app=access.opportunity.deliver_app)
        due_date = date.today() + timedelta(days=7)
        AssignedTaskFactory(task_type=task_type, opportunity_access=access, status=AssignedTaskStatus.COMPLETED)

        assigned = AssignedTask.assign(task_type=task_type, opportunity_access=access, due_date=due_date)

        assert AssignedTask.objects.filter(task_type=task_type, opportunity_access=access).count() == 2
        assert assigned.status == AssignedTaskStatus.ASSIGNED

    def test_does_not_create_row_when_hq_call_fails(self):
        access = OpportunityAccessFactory()
        task_type = TaskTypeFactory(app=access.opportunity.deliver_app, case_property="needs_assessment")
        due_date = date.today() + timedelta(days=7)

        with mock.patch(
            "commcare_connect.commcarehq.api.bulk_update_usercases",
            side_effect=CommCareHQAPIException("boom"),
        ):
            with pytest.raises(CommCareHQAPIException):
                AssignedTask.assign(
                    task_type=task_type,
                    opportunity_access=access,
                    due_date=due_date,
                )

        assert not AssignedTask.objects.filter(opportunity_access=access, task_type=task_type).exists()

    def test_triggers_ocs_bot_and_persists_session_for_ocs_mode(self):
        access = OpportunityAccessFactory()
        access.user.phone_number = "+15551234567"
        access.user.save()
        task_type = TaskTypeFactory(
            app=access.opportunity.deliver_app,
            mode=TaskTypeModeChoices.OCS,
            ocs_chatbot_id="exp-uuid",
        )
        due_date = date.today() + timedelta(days=7)
        assigner = access.user

        with mock.patch(
            "commcare_connect.utils.ocs_api.trigger_bot",
            return_value={"session_id": "sess-1", "channel": "chan-1"},
        ) as mock_trigger:
            assigned = AssignedTask.assign(
                task_type=task_type,
                opportunity_access=access,
                due_date=due_date,
                assigned_by=assigner,
            )

        mock_trigger.assert_called_once_with(
            assigner,
            identifier="+15551234567",
            experiment="exp-uuid",
            participant_data={"connectTaskId": str(assigned.assigned_task_id)},
        )
        assigned.refresh_from_db()
        assert assigned.ocs_session_id == "sess-1"
        assert assigned.connect_channel_id == "chan-1"

    def test_does_not_trigger_ocs_bot_for_non_ocs_mode(self):
        access = OpportunityAccessFactory()
        task_type = TaskTypeFactory(app=access.opportunity.deliver_app, mode=TaskTypeModeChoices.RELEARN)
        due_date = date.today() + timedelta(days=7)

        with mock.patch("commcare_connect.utils.ocs_api.trigger_bot") as mock_trigger:
            AssignedTask.assign(task_type=task_type, opportunity_access=access, due_date=due_date)

        mock_trigger.assert_not_called()

    def test_does_not_create_row_when_ocs_trigger_fails(self):
        access = OpportunityAccessFactory()
        access.user.phone_number = "+15551234567"
        access.user.save()
        task_type = TaskTypeFactory(
            app=access.opportunity.deliver_app,
            mode=TaskTypeModeChoices.OCS,
            ocs_chatbot_id="exp-uuid",
            case_property="needs_assessment",
        )
        due_date = date.today() + timedelta(days=7)

        with (
            mock.patch(
                "commcare_connect.utils.ocs_api.trigger_bot",
                side_effect=OcsApiError("boom"),
            ),
            mock.patch("commcare_connect.commcarehq.api.bulk_update_usercases") as mock_update,
        ):
            with pytest.raises(OcsApiError):
                AssignedTask.assign(
                    task_type=task_type,
                    opportunity_access=access,
                    due_date=due_date,
                    assigned_by=access.user,
                )

        assert not AssignedTask.objects.filter(opportunity_access=access, task_type=task_type).exists()
        mock_update.assert_not_called()

    def test_ocs_mode_skips_hq_even_with_case_property(self):
        access = OpportunityAccessFactory()
        access.user.phone_number = "+15551234567"
        access.user.save()
        task_type = TaskTypeFactory(
            app=access.opportunity.deliver_app,
            mode=TaskTypeModeChoices.OCS,
            ocs_chatbot_id="exp-uuid",
            case_property="needs_assessment",
        )
        due_date = date.today() + timedelta(days=7)

        with (
            mock.patch(
                "commcare_connect.utils.ocs_api.trigger_bot",
                return_value={"session_id": "s", "channel": "c"},
            ) as mock_trigger,
            mock.patch("commcare_connect.commcarehq.api.bulk_update_usercases") as mock_hq,
        ):
            AssignedTask.assign(
                task_type=task_type,
                opportunity_access=access,
                due_date=due_date,
                assigned_by=access.user,
            )

        mock_trigger.assert_called_once()
        mock_hq.assert_not_called()


@pytest.mark.django_db
class TestAssignedTaskMarkCompleted:
    def test_transitions_to_completed_and_notifies(self, django_capture_on_commit_callbacks):
        task = AssignedTaskFactory(status=AssignedTaskStatus.ASSIGNED)
        completed_at = now()

        with (
            mock.patch("commcare_connect.opportunity.tasks.send_task_completion_notification") as notify,
            django_capture_on_commit_callbacks(execute=True),
        ):
            task.mark_completed(completed_at=completed_at)

        task.refresh_from_db()
        assert task.status == AssignedTaskStatus.COMPLETED
        assert task.completed_at == completed_at
        notify.delay.assert_called_once_with(task.pk)

    def test_send_notification_false_suppresses_email(self, django_capture_on_commit_callbacks):
        task = AssignedTaskFactory(status=AssignedTaskStatus.ASSIGNED)

        with (
            mock.patch("commcare_connect.opportunity.tasks.send_task_completion_notification") as notify,
            django_capture_on_commit_callbacks(execute=True),
        ):
            task.mark_completed(completed_at=now(), send_notification=False)

        task.refresh_from_db()
        assert task.status == AssignedTaskStatus.COMPLETED
        notify.delay.assert_not_called()

    def test_defaults_completed_at_to_now(self):
        task = AssignedTaskFactory(status=AssignedTaskStatus.ASSIGNED, completed_at=None)
        before = now()

        task.mark_completed()

        task.refresh_from_db()
        assert task.completed_at >= before

    def test_stores_optional_form_fields(self):
        task = AssignedTaskFactory(status=AssignedTaskStatus.ASSIGNED)

        task.mark_completed(
            completed_at=now(),
            xform_id="form-1",
            duration=timedelta(minutes=3),
            app_build_id="build-1",
            app_build_version=7,
        )

        task.refresh_from_db()
        assert task.xform_id == "form-1"
        assert task.duration == timedelta(minutes=3)
        assert task.app_build_id == "build-1"
        assert task.app_build_version == 7

    def test_bumps_last_active_when_newer(self):
        task = AssignedTaskFactory(status=AssignedTaskStatus.ASSIGNED)
        access = task.opportunity_access
        access.last_active = now() - timedelta(days=2)
        access.save(update_fields=["last_active"])
        completed_at = now()

        task.mark_completed(completed_at=completed_at)

        access.refresh_from_db()
        assert access.last_active == completed_at

    def test_is_idempotent_when_already_completed(self, django_capture_on_commit_callbacks):
        original = now() - timedelta(days=1)
        task = AssignedTaskFactory(status=AssignedTaskStatus.COMPLETED, completed_at=original)

        with (
            mock.patch("commcare_connect.opportunity.tasks.send_task_completion_notification") as notify,
            django_capture_on_commit_callbacks(execute=True),
        ):
            task.mark_completed(completed_at=now())

        task.refresh_from_db()
        assert task.completed_at == original
        notify.delay.assert_not_called()


@pytest.mark.django_db
class TestAssignedTaskDeleteAndResetHQ:
    def test_deletes_rows_and_resets_hq(self):
        access = OpportunityAccessFactory()
        task_type = TaskTypeFactory(app=access.opportunity.deliver_app, case_property="needs_assessment")
        due_date = date.today() + timedelta(days=7)
        task = AssignedTaskFactory(
            task_type=task_type,
            opportunity_access=access,
            status=AssignedTaskStatus.ASSIGNED,
            due_date=due_date,
        )

        with mock.patch("commcare_connect.commcarehq.api.bulk_update_usercases") as mock_update:
            deleted = AssignedTask.bulk_delete([task.pk], access.opportunity)

        assert deleted == 1
        assert not AssignedTask.objects.filter(pk=task.pk).exists()
        mock_update.assert_called_once_with({access: {"properties": {"needs_assessment": ""}}})

    @pytest.mark.parametrize("case_property", [None, ""])
    def test_skips_hq_when_no_case_property(self, case_property):
        access = OpportunityAccessFactory()
        task_type = TaskTypeFactory(app=access.opportunity.deliver_app, case_property=case_property)
        task = AssignedTaskFactory(
            task_type=task_type,
            opportunity_access=access,
            status=AssignedTaskStatus.ASSIGNED,
            due_date=date.today() + timedelta(days=7),
        )

        with mock.patch("commcare_connect.commcarehq.api.bulk_update_usercases") as mock_update:
            deleted = AssignedTask.bulk_delete([task.pk], access.opportunity)

        assert deleted == 1
        mock_update.assert_not_called()

    def test_does_not_delete_when_hq_fails(self):
        access = OpportunityAccessFactory()
        task_type = TaskTypeFactory(app=access.opportunity.deliver_app, case_property="needs_assessment")
        task = AssignedTaskFactory(
            task_type=task_type,
            opportunity_access=access,
            status=AssignedTaskStatus.ASSIGNED,
            due_date=date.today() + timedelta(days=7),
        )

        with mock.patch(
            "commcare_connect.commcarehq.api.bulk_update_usercases",
            side_effect=CommCareHQAPIException("boom"),
        ):
            with pytest.raises(CommCareHQAPIException):
                AssignedTask.bulk_delete([task.pk], access.opportunity)

        assert AssignedTask.objects.filter(pk=task.pk).exists()

    def test_skips_completed_tasks(self):
        access = OpportunityAccessFactory()
        task_type = TaskTypeFactory(app=access.opportunity.deliver_app, case_property="needs_assessment")
        completed = AssignedTaskFactory(
            task_type=task_type,
            opportunity_access=access,
            status=AssignedTaskStatus.COMPLETED,
            due_date=date.today() + timedelta(days=7),
        )

        with mock.patch("commcare_connect.commcarehq.api.bulk_update_usercases") as mock_update:
            deleted = AssignedTask.bulk_delete([completed.pk], access.opportunity)

        assert deleted == 0
        assert AssignedTask.objects.filter(pk=completed.pk).exists()
        mock_update.assert_not_called()

    def test_skips_hq_when_other_assigned_task_remains(self):
        access = OpportunityAccessFactory()
        task_type = TaskTypeFactory(app=access.opportunity.deliver_app, case_property="needs_assessment")
        task_type2 = TaskTypeFactory(app=access.opportunity.deliver_app, case_property="needs_assessment")
        task_to_delete = AssignedTaskFactory(
            task_type=task_type,
            opportunity_access=access,
            status=AssignedTaskStatus.ASSIGNED,
            due_date=date.today() + timedelta(days=7),
        )
        AssignedTaskFactory(
            task_type=task_type2,
            opportunity_access=access,
            status=AssignedTaskStatus.ASSIGNED,
            due_date=date.today() + timedelta(days=7),
        )

        with mock.patch("commcare_connect.commcarehq.api.bulk_update_usercases") as mock_update:
            deleted = AssignedTask.bulk_delete([task_to_delete.pk], access.opportunity)

        assert deleted == 1
        assert not AssignedTask.objects.filter(pk=task_to_delete.pk).exists()
        mock_update.assert_not_called()

    def test_deduplicates_hq_calls(self):
        access = OpportunityAccessFactory()
        due_date = date.today() + timedelta(days=7)
        tasks = AssignedTaskFactory.create_batch(
            2,
            task_type__app=access.opportunity.deliver_app,
            task_type__case_property="needs_assessment",
            opportunity_access=access,
            status=AssignedTaskStatus.ASSIGNED,
            due_date=due_date,
        )

        with mock.patch("commcare_connect.commcarehq.api.bulk_update_usercases") as mock_update:
            AssignedTask.bulk_delete([t.pk for t in tasks], access.opportunity)

        mock_update.assert_called_once_with({access: {"properties": {"needs_assessment": ""}}})

    def test_merges_multiple_properties_per_user(self):
        access = OpportunityAccessFactory()
        due_date = date.today() + timedelta(days=7)
        task_type_a = TaskTypeFactory(app=access.opportunity.deliver_app, case_property="prop_a")
        task_type_b = TaskTypeFactory(app=access.opportunity.deliver_app, case_property="prop_b")
        task_a = AssignedTaskFactory(
            task_type=task_type_a, opportunity_access=access, status=AssignedTaskStatus.ASSIGNED, due_date=due_date
        )
        task_b = AssignedTaskFactory(
            task_type=task_type_b, opportunity_access=access, status=AssignedTaskStatus.ASSIGNED, due_date=due_date
        )

        with mock.patch("commcare_connect.commcarehq.api.bulk_update_usercases") as mock_update:
            AssignedTask.bulk_delete([task_a.pk, task_b.pk], access.opportunity)

        mock_update.assert_called_once_with({access: {"properties": {"prop_a": "", "prop_b": ""}}})


@pytest.mark.django_db
def test_audio_attachment_unique_per_visit_and_name():
    visit = UserVisitFactory.create()
    AudioAttachment.objects.create(user_visit=visit, name="recording.m4a", content_length=1)
    with pytest.raises(IntegrityError):
        AudioAttachment.objects.create(user_visit=visit, name="recording.m4a", content_length=1)
