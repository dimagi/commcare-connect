import datetime
from decimal import Decimal
from unittest import mock

import pytest
from django.utils.timezone import now
from waffle.testutils import override_switch

from commcare_connect.connect_id_client.models import ConnectIdUser
from commcare_connect.flags.switch_names import AUTOMATED_INVOICES_MONTHLY
from commcare_connect.opportunity.models import (
    BlobMeta,
    CompletedWorkStatus,
    ExchangeRate,
    InvoiceStatus,
    Opportunity,
    OpportunityAccess,
    PaymentInvoice,
)
from commcare_connect.opportunity.tasks import (
    _get_inactive_message,
    add_connect_users,
    download_user_visit_attachments,
    generate_automated_service_delivery_invoice,
)
from commcare_connect.opportunity.tests.factories import (
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
        download_user_visit_attachments(user_visit.id)
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

    @override_switch(AUTOMATED_INVOICES_MONTHLY, active=True)
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
        assert invoice1.status == InvoiceStatus.PENDING
        assert invoice1.start_date == datetime.date(2024, 1, 1)
        assert invoice1.end_date == datetime.date(2024, 1, 31)
        assert invoice1.invoice_number == "INV001"
        assert completed_work1.invoice == invoice1

        invoice2 = PaymentInvoice.objects.get(opportunity=opportunity2)
        assert invoice2.amount == Decimal("50.00")
        assert invoice2.status == InvoiceStatus.PENDING
        assert invoice2.start_date == datetime.date(2024, 1, 1)
        assert invoice2.end_date == datetime.date(2024, 1, 31)
        assert invoice2.invoice_number == "INV002"
        assert completed_work2.invoice == invoice2

    @override_switch(AUTOMATED_INVOICES_MONTHLY, active=True)
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

    @override_switch(AUTOMATED_INVOICES_MONTHLY, active=True)
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

    @override_switch(AUTOMATED_INVOICES_MONTHLY, active=False)
    def test_no_invoice_when_switch_inactive(self):
        opportunity = OpportunityFactory(active=True, managed=True, start_date=datetime.date(2026, 1, 1))
        payment_unit = PaymentUnitFactory(opportunity=opportunity, amount=Decimal("100.00"))
        access = OpportunityAccessFactory(opportunity=opportunity)
        completed_work = CompletedWorkFactory(
            opportunity_access=access,
            payment_unit=payment_unit,
            status=CompletedWorkStatus.approved,
            status_modified_date=now(),
        )
        completed_work.saved_payment_accrued = Decimal("100.00")
        completed_work.save()

        generate_automated_service_delivery_invoice()

        assert PaymentInvoice.objects.count() == 0
        assert completed_work.invoice is None

    @override_switch(AUTOMATED_INVOICES_MONTHLY, active=True)
    def test_no_invoice_with_no_approved_work(self):
        opportunity = OpportunityFactory(active=True, managed=True, start_date=datetime.date(2026, 1, 1))

        generate_automated_service_delivery_invoice()

        invoice = PaymentInvoice.objects.filter(opportunity=opportunity)
        assert len(invoice) == 0
