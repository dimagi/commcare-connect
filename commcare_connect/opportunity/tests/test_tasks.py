import datetime
from decimal import Decimal
from unittest import mock

import pytest
from django.utils.timezone import now
from tablib import Dataset

from commcare_connect.connect_id_client.models import ConnectIdUser, Message
from commcare_connect.opportunity.models import (
    BlobMeta,
    CompletedWorkStatus,
    ExchangeRate,
    InvoiceStatus,
    Opportunity,
    OpportunityAccess,
    OpportunityActiveEvent,
    PaymentInvoice,
    UserInvite,
)
from commcare_connect.opportunity.tasks import (
    _get_inactive_message,
    add_connect_users,
    auto_deactivate_ended_opportunities,
    download_user_visit_attachments,
    generate_automated_service_delivery_invoice,
    generate_catchment_area_export,
    generate_deliver_status_export,
    generate_payment_export,
    generate_review_visit_export,
    generate_user_status_export,
    generate_visit_export,
    generate_work_status_export,
    notify_user_for_scored_assessment,
    save_export,
)
from commcare_connect.opportunity.tests.factories import (
    AssessmentFactory,
    CompletedModuleFactory,
    CompletedWorkFactory,
    LearnModuleFactory,
    OpportunityAccessFactory,
    OpportunityClaimFactory,
    OpportunityFactory,
    PaymentUnitFactory,
    UserVisitFactory,
)
from commcare_connect.users.models import User


class TestConnectUserCreation:
    @pytest.mark.django_db
    def test_add_connect_user(self, httpx_mock):
        opportunity = OpportunityFactory()
        with (
            mock.patch("commcare_connect.opportunity.tasks.fetch_users") as fetch_users,
            mock.patch("commcare_connect.opportunity.tasks.invite_user"),
        ):
            fetch_users.return_value = [
                ConnectIdUser(username="test", phone_number="+15555555555", name="a"),
                ConnectIdUser(username="test2", phone_number="+12222222222", name="b"),
            ]
            add_connect_users(["+15555555555", "+12222222222"], opportunity.id)

        user_list = User.objects.filter(username="test")
        assert len(user_list) == 1
        user = user_list[0]
        assert user.name == "a"
        assert user.phone_number == "+15555555555"
        assert len(OpportunityAccess.objects.filter(user=user_list.first(), opportunity=opportunity)) == 1

        user2 = User.objects.filter(username="test2")
        assert len(user2) == 1
        assert len(OpportunityAccess.objects.filter(user=user2.first(), opportunity=opportunity)) == 1

    @pytest.mark.django_db
    def test_add_connect_users_skips_ended_opportunity(self):
        opportunity = OpportunityFactory(end_date=datetime.date.today() - datetime.timedelta(days=1))
        with mock.patch("commcare_connect.opportunity.tasks.fetch_users") as fetch_users:
            fetch_users.return_value = [
                ConnectIdUser(username="test", phone_number="+15555555555", name="a"),
            ]
            add_connect_users(["+15555555555"], opportunity.id)

        fetch_users.assert_not_called()
        assert User.objects.filter(username="test").count() == 0
        assert OpportunityAccess.objects.filter(opportunity=opportunity).count() == 0
        assert UserInvite.objects.filter(opportunity=opportunity).count() == 0


def test_send_inactive_notification_learn_inactive_message(mobile_user: User, opportunity: Opportunity):
    learn_modules = LearnModuleFactory.create_batch(2, app=opportunity.learn_app)
    access = OpportunityAccess.objects.get(user=mobile_user, opportunity=opportunity)
    CompletedModuleFactory.create(
        date=now() - datetime.timedelta(days=3),
        user=mobile_user,
        opportunity=opportunity,
        module=learn_modules[0],
        opportunity_access=access,
    )
    access.refresh_from_db()
    message = _get_inactive_message(access)
    assert message is not None
    assert message.usernames[0] == mobile_user.username
    assert message.data.get("title") == f"Resume your learning journey for {opportunity.name}"


def test_send_inactive_notification_deliver_inactive_message(mobile_user: User, opportunity: Opportunity):
    learn_modules = LearnModuleFactory.create_batch(2, app=opportunity.learn_app)
    access = OpportunityAccess.objects.get(user=mobile_user, opportunity=opportunity)
    for learn_module in learn_modules:
        CompletedModuleFactory.create(
            user=mobile_user,
            opportunity=opportunity,
            module=learn_module,
            date=now() - datetime.timedelta(days=2),
            opportunity_access=access,
        )
    access.refresh_from_db()
    OpportunityClaimFactory.create(opportunity_access=access, end_date=opportunity.end_date)
    UserVisitFactory.create(
        user=mobile_user,
        opportunity=opportunity,
        visit_date=now() - datetime.timedelta(days=2),
        opportunity_access=access,
    )

    message = _get_inactive_message(access)
    assert message is not None
    assert message.usernames[0] == mobile_user.username
    assert message.data.get("title") == f"Resume your job for {opportunity.name}"


def test_send_inactive_notification_not_claimed_deliver_message(mobile_user: User, opportunity: Opportunity):
    learn_modules = LearnModuleFactory.create_batch(2, app=opportunity.learn_app)
    access = OpportunityAccess.objects.get(user=mobile_user, opportunity=opportunity)
    for learn_module in learn_modules:
        CompletedModuleFactory.create(
            user=mobile_user,
            opportunity=opportunity,
            module=learn_module,
            date=now() - datetime.timedelta(days=2),
            opportunity_access=access,
        )
    message = _get_inactive_message(access)
    assert message is not None
    assert message.usernames[0] == mobile_user.username
    assert message.data.get("title") == f"Resume your job for {opportunity.name}"


def test_send_inactive_notification_active_user(mobile_user: User, opportunity: Opportunity):
    learn_modules = LearnModuleFactory.create_batch(2, app=opportunity.learn_app)
    access = OpportunityAccess.objects.get(user=mobile_user, opportunity=opportunity)
    for learn_module in learn_modules:
        CompletedModuleFactory.create(
            user=mobile_user,
            opportunity=opportunity,
            module=learn_module,
            date=now() - datetime.timedelta(days=2),
            opportunity_access=access,
        )
    access = OpportunityAccess.objects.get(user=mobile_user, opportunity=opportunity)
    OpportunityClaimFactory.create(opportunity_access=access, end_date=opportunity.end_date)
    UserVisitFactory.create(
        user=mobile_user,
        opportunity=opportunity,
        visit_date=now() - datetime.timedelta(days=1),
        opportunity_access=access,
    )
    message = _get_inactive_message(access)
    assert message is None


def test_download_attachments(mobile_user: User, opportunity: Opportunity):
    learn_modules = LearnModuleFactory.create_batch(2, app=opportunity.learn_app)
    for learn_module in learn_modules:
        CompletedModuleFactory.create(
            user=mobile_user,
            opportunity=opportunity,
            module=learn_module,
            date=now() - datetime.timedelta(days=2),
        )
    access = OpportunityAccess.objects.get(user=mobile_user, opportunity=opportunity)
    OpportunityClaimFactory.create(opportunity_access=access, end_date=opportunity.end_date)
    user_visit = UserVisitFactory.create(
        user=mobile_user,
        opportunity=opportunity,
        form_json={"attachments": {"myimage.jpg": {"content_type": "image/jpeg", "length": 20}}},
    )
    with mock.patch("commcare_connect.opportunity.tasks.httpx.get") as get_response, mock.patch(
        "commcare_connect.opportunity.tasks.default_storage.save"
    ) as save_blob:
        get_response.return_value.content = b"asdas"
        download_user_visit_attachments.run(user_visit.id)
        blob_meta = BlobMeta.objects.first()

        assert blob_meta.name == "myimage.jpg"
        assert blob_meta.parent_id == user_visit.xform_id
        assert blob_meta.content_length == 20
        assert blob_meta.content_type == "image/jpeg"
        blob_id, content_file = save_blob.call_args_list[0].args
        assert str(blob_id) == blob_meta.blob_id
        assert content_file.read() == b"asdas"


@pytest.mark.django_db
class TestGenerateAutomatedServiceDeliveryInvoice:
    @pytest.fixture(autouse=True)
    def setup_mocks(self):
        with (
            mock.patch("commcare_connect.opportunity.tasks.get_start_date_for_invoice") as mock_start_date,
            mock.patch("commcare_connect.opportunity.tasks.get_end_date_previous_month") as mock_end_date,
            mock.patch("commcare_connect.opportunity.tasks.generate_invoice_number") as mock_invoice_number,
            mock.patch(
                "commcare_connect.opportunity.models.ExchangeRate.latest_exchange_rate",
                create=True,
            ) as mock_latest_exchange_rate,
        ):
            mock_start_date.return_value = datetime.date(2024, 1, 1)
            mock_end_date.return_value = datetime.date(2024, 1, 31)
            mock_invoice_number.side_effect = ["INV001", "INV002", "INV003", "INV004", "INV005"]

            rate = ExchangeRate.objects.create(
                currency_code="USD", rate=Decimal("1.00"), rate_date=datetime.date(2024, 1, 31)
            )
            mock_latest_exchange_rate.return_value = rate

            self.mock_start_date = mock_start_date
            self.mock_end_date = mock_end_date
            self.mock_invoice_number = mock_invoice_number

            yield

    def test_generate_invoice_for_two_active_managed_opportunities(self):
        opportunity1 = OpportunityFactory(active=True, managed=True, start_date=datetime.date(2026, 1, 1))
        opportunity2 = OpportunityFactory(active=True, managed=True, start_date=datetime.date(2026, 12, 1))

        payment_unit1 = PaymentUnitFactory(opportunity=opportunity1, amount=Decimal("100.00"))
        payment_unit2 = PaymentUnitFactory(opportunity=opportunity2, amount=Decimal("50.00"))

        access1 = OpportunityAccessFactory(opportunity=opportunity1)
        access2 = OpportunityAccessFactory(opportunity=opportunity2)

        completed_work1 = CompletedWorkFactory(
            opportunity_access=access1,
            payment_unit=payment_unit1,
            status=CompletedWorkStatus.approved,
            status_modified_date=datetime.date(2024, 1, 4),
        )
        completed_work1.saved_payment_accrued = Decimal("100.00")
        completed_work1.save()
        completed_work2 = CompletedWorkFactory(
            opportunity_access=access2,
            payment_unit=payment_unit2,
            status=CompletedWorkStatus.approved,
            status_modified_date=datetime.date(2024, 1, 5),
        )
        completed_work2.saved_payment_accrued = Decimal("50.00")
        completed_work2.save()

        generate_automated_service_delivery_invoice()

        invoices = PaymentInvoice.objects.all()
        assert invoices.count() == 2

        completed_work1.refresh_from_db()
        completed_work2.refresh_from_db()

        invoice1 = PaymentInvoice.objects.get(opportunity=opportunity1)
        assert invoice1.amount == Decimal("100.00")
        assert invoice1.status == InvoiceStatus.PENDING_NM_REVIEW
        assert invoice1.start_date == datetime.date(2024, 1, 1)
        assert invoice1.end_date == datetime.date(2024, 1, 31)
        assert invoice1.invoice_number == "INV001"
        assert completed_work1.invoice == invoice1

        invoice2 = PaymentInvoice.objects.get(opportunity=opportunity2)
        assert invoice2.amount == Decimal("50.00")
        assert invoice2.status == InvoiceStatus.PENDING_NM_REVIEW
        assert invoice2.start_date == datetime.date(2024, 1, 1)
        assert invoice2.end_date == datetime.date(2024, 1, 31)
        assert invoice2.invoice_number == "INV002"
        assert completed_work2.invoice == invoice2

    def test_no_invoice_for_inactive_opportunities(self):
        inactive_opportunity = OpportunityFactory(active=False, managed=True, start_date=datetime.date(2026, 1, 1))
        payment_unit = PaymentUnitFactory(opportunity=inactive_opportunity)
        access = OpportunityAccessFactory(opportunity=inactive_opportunity)
        completed_work = CompletedWorkFactory(
            opportunity_access=access,
            payment_unit=payment_unit,
            status=CompletedWorkStatus.approved,
            status_modified_date=datetime.date(2024, 1, 5),
        )
        completed_work.saved_payment_accrued = Decimal("100.00")
        completed_work.save()

        generate_automated_service_delivery_invoice()

        completed_work.refresh_from_db()

        assert PaymentInvoice.objects.count() == 0
        assert completed_work.invoice is None

    def test_no_invoice_for_active_unmanaged_opportunity(self):
        unmanaged_opportunity = OpportunityFactory(active=True, managed=False, start_date=datetime.date(2026, 1, 1))
        payment_unit = PaymentUnitFactory(opportunity=unmanaged_opportunity, amount=Decimal("100.00"))
        access = OpportunityAccessFactory(opportunity=unmanaged_opportunity)
        completed_work = CompletedWorkFactory(
            opportunity_access=access,
            payment_unit=payment_unit,
            status=CompletedWorkStatus.approved,
            status_modified_date=datetime.date(2024, 1, 5),
        )
        completed_work.saved_payment_accrued = Decimal("100.00")
        completed_work.save()

        generate_automated_service_delivery_invoice()

        assert PaymentInvoice.objects.count() == 0
        assert completed_work.invoice is None

    def test_no_invoice_with_no_approved_work(self):
        opportunity = OpportunityFactory(active=True, managed=True, start_date=datetime.date(2026, 1, 1))

        generate_automated_service_delivery_invoice()

        invoice = PaymentInvoice.objects.filter(opportunity=opportunity)
        assert len(invoice) == 0


@pytest.mark.django_db
class TestAutoDeactivateEndedOpportunities:
    def test_deactivates_opportunities_30_days_after_end(self):
        cutoff = datetime.date.today() - datetime.timedelta(days=30)
        opp_to_deactivate = OpportunityFactory(active=True, end_date=cutoff)
        opp_still_active = OpportunityFactory(active=True, end_date=datetime.date.today())
        opp_already_inactive = OpportunityFactory(active=False, end_date=cutoff)

        auto_deactivate_ended_opportunities()

        opp_to_deactivate.refresh_from_db()
        opp_still_active.refresh_from_db()
        opp_already_inactive.refresh_from_db()

        assert opp_to_deactivate.active is False
        assert opp_still_active.active is True
        assert opp_already_inactive.active is False  # unchanged

    def test_records_pghistory_event_with_system_context(self):
        cutoff = datetime.date.today() - datetime.timedelta(days=30)
        opp = OpportunityFactory(active=True, end_date=cutoff)

        auto_deactivate_ended_opportunities()

        # Should have 2 events: initial create + the deactivation
        events = OpportunityActiveEvent.objects.filter(pgh_obj=opp).order_by("pgh_id")
        assert events.count() == 1
        deactivation_event = events.last()
        assert deactivation_event.active is False
        assert deactivation_event.pgh_context is not None
        assert deactivation_event.pgh_context.metadata["username"] == "system"
        assert (
            deactivation_event.pgh_context.metadata["action"]
            == "commcare_connect.opportunity.tasks.auto_deactivate_ended_opportunities"
        )


@mock.patch("commcare_connect.opportunity.tasks.send_message")
def test_notify_user_for_scored_assessment(send_message_patch):
    assessment = AssessmentFactory()
    notify_user_for_scored_assessment(assessment.pk)
    assert send_message_patch.call_count == 1
    send_message_patch.assert_called_with(
        Message(
            usernames=[assessment.user.username],
            data={
                "action": "ccc_generic_opportunity",
                "key": "scored_assessment",
                "opportunity_status": "learn",
                "opportunity_id": str(assessment.opportunity.id),
                "opportunity_uuid": str(assessment.opportunity.opportunity_id),
                "title": "Update on your Assessment",
                "body": f"Assessment for opportunity '{assessment.opportunity.name}' scored, check your status",
            },
        )
    )


def test_save_export_uses_export_storage():
    mock_storage_cls = mock.MagicMock()
    mock_storage_cls.return_value.save.side_effect = lambda name, content: name
    dataset = Dataset(["val1", "val2"], headers=["col1", "col2"])
    filename = "2026-03-09T10:00:00_test_visit_export.csv"

    with mock.patch.dict(
        "sys.modules", {"commcare_connect.utils.storages": mock.MagicMock(ExportS3Boto3Storage=mock_storage_cls)}
    ):
        result = save_export(dataset, filename, "csv")

    mock_storage_cls.return_value.save.assert_called_once()
    assert result == filename


@pytest.mark.django_db
class TestExportTasksCreateExportFile:
    @mock.patch("commcare_connect.opportunity.tasks.save_export")
    @mock.patch("commcare_connect.opportunity.tasks.UserVisitExporter")
    def test_generate_visit_export(self, mock_exporter_cls, mock_save, opportunity):
        mock_exporter_cls.return_value.get_dataset.return_value = Dataset()
        generate_visit_export(opportunity.id, None, None, [], "csv", False)
        mock_save.assert_called_once()
        args = mock_save.call_args[0]
        assert args[1].endswith("_visit_export.csv")
        assert args[2] == "csv"

    @mock.patch("commcare_connect.opportunity.tasks.save_export")
    @mock.patch("commcare_connect.opportunity.tasks.export_user_visit_review_data")
    def test_generate_review_visit_export(self, mock_export_fn, mock_save, opportunity):
        mock_export_fn.return_value = Dataset()
        generate_review_visit_export(opportunity.id, None, None, [], "csv")
        mock_save.assert_called_once()
        args = mock_save.call_args[0]
        assert args[1].endswith("_review_visit_export.csv")
        assert args[2] == "csv"

    @mock.patch("commcare_connect.opportunity.tasks.save_export")
    @mock.patch("commcare_connect.opportunity.tasks.export_empty_payment_table")
    def test_generate_payment_export(self, mock_export_fn, mock_save, opportunity):
        mock_export_fn.return_value = Dataset()
        generate_payment_export(opportunity.id, "csv")
        mock_save.assert_called_once()
        args = mock_save.call_args[0]
        assert args[1].endswith("_payment_export.csv")
        assert args[2] == "csv"

    @mock.patch("commcare_connect.opportunity.tasks.save_export")
    @mock.patch("commcare_connect.opportunity.tasks.export_user_status_table")
    def test_generate_user_status_export(self, mock_export_fn, mock_save, opportunity):
        mock_export_fn.return_value = Dataset()
        generate_user_status_export(opportunity.id, "csv")
        mock_save.assert_called_once()
        args = mock_save.call_args[0]
        assert args[1].endswith("_user_status.csv")
        assert args[2] == "csv"

    @mock.patch("commcare_connect.opportunity.tasks.save_export")
    @mock.patch("commcare_connect.opportunity.tasks.export_deliver_status_table")
    def test_generate_deliver_status_export(self, mock_export_fn, mock_save, opportunity):
        mock_export_fn.return_value = Dataset()
        generate_deliver_status_export(opportunity.id, "csv")
        mock_save.assert_called_once()
        args = mock_save.call_args[0]
        assert args[1].endswith("_deliver_status.csv")
        assert args[2] == "csv"

    @mock.patch("commcare_connect.opportunity.tasks.save_export")
    @mock.patch("commcare_connect.opportunity.tasks.export_work_status_table")
    def test_generate_work_status_export(self, mock_export_fn, mock_save, opportunity):
        mock_export_fn.return_value = Dataset()
        generate_work_status_export(opportunity.id, "csv")
        mock_save.assert_called_once()
        args = mock_save.call_args[0]
        assert args[1].endswith("_work_status.csv")
        assert args[2] == "csv"

    @mock.patch("commcare_connect.opportunity.tasks.save_export")
    @mock.patch("commcare_connect.opportunity.tasks.export_catchment_area_table")
    def test_generate_catchment_area_export(self, mock_export_fn, mock_save, opportunity):
        mock_export_fn.return_value = Dataset()
        generate_catchment_area_export(opportunity.id, "csv")
        mock_save.assert_called_once()
        args = mock_save.call_args[0]
        assert args[1].endswith("_catchment_area.csv")
        assert args[2] == "csv"
