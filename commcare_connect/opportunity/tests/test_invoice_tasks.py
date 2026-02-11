"""Tests for invoice-related Celery tasks."""
import pytest
from unittest.mock import Mock, patch, MagicMock
from datetime import date, datetime, timedelta
from decimal import Decimal

from commcare_connect.opportunity.models import (
    InvoiceStatus,
    PaymentInvoice,
)
from commcare_connect.opportunity.tasks import (
    send_invoice_paid_mail,
    generate_automated_service_delivery_invoice,
    _bulk_create_and_link_invoices,
    _send_auto_invoice_created_notification,
)
from commcare_connect.opportunity.tests.factories import (
    CompletedWorkFactory,
    ExchangeRateFactory,
    OpportunityFactory,
    OpportunityAccessFactory,
    PaymentInvoiceFactory,
    PaymentUnitFactory,
)
from commcare_connect.organization.models import OrganizationMembership
from commcare_connect.users.tests.factories import OrganizationFactory, UserFactory


@pytest.mark.django_db
class TestSendInvoicePaidMail:
    @patch("commcare_connect.opportunity.tasks.send_mail")
    def test_send_invoice_paid_mail_success(self, mock_send_mail):
        """Test that invoice paid mail is sent successfully."""
        user = UserFactory()
        organization = OrganizationFactory()
        OrganizationMembership.objects.create(user=user, organization=organization, is_admin=True)

        opportunity = OpportunityFactory(organization=organization)
        invoice = PaymentInvoiceFactory(opportunity=opportunity, status=InvoiceStatus.APPROVED)

        send_invoice_paid_mail(opportunity.id, [invoice.id])

        # Verify email was sent
        assert mock_send_mail.called
        call_args = mock_send_mail.call_args
        assert invoice.invoice_number in call_args.kwargs["subject"]
        assert user.email in call_args.kwargs["recipient_list"]

    @patch("commcare_connect.opportunity.tasks.send_mail")
    def test_send_invoice_paid_mail_no_recipients(self, mock_send_mail):
        """Test that no email is sent when organization has no members."""
        organization = OrganizationFactory()
        opportunity = OpportunityFactory(organization=organization)
        invoice = PaymentInvoiceFactory(opportunity=opportunity)

        send_invoice_paid_mail(opportunity.id, [invoice.id])

        # No email should be sent
        assert not mock_send_mail.called

    @patch("commcare_connect.opportunity.tasks.send_mail")
    def test_send_invoice_paid_mail_multiple_invoices(self, mock_send_mail):
        """Test sending emails for multiple invoices."""
        user = UserFactory()
        organization = OrganizationFactory()
        OrganizationMembership.objects.create(user=user, organization=organization, is_admin=True)

        opportunity = OpportunityFactory(organization=organization)
        invoice1 = PaymentInvoiceFactory(opportunity=opportunity)
        invoice2 = PaymentInvoiceFactory(opportunity=opportunity)

        send_invoice_paid_mail(opportunity.id, [invoice1.id, invoice2.id])

        # Should send 2 emails
        assert mock_send_mail.call_count == 2


@pytest.mark.django_db
class TestBulkCreateAndLinkInvoices:
    @patch("commcare_connect.opportunity.tasks.link_invoice_to_completed_works")
    def test_bulk_create_and_link_invoices(self, mock_link):
        """Test bulk creation and linking of invoices."""
        opportunity = OpportunityFactory()
        start_date = date(2025, 1, 1)
        end_date = date(2025, 1, 31)
        exchange_rate = ExchangeRateFactory()

        invoices_chunk = [
            PaymentInvoice(
                opportunity=opportunity,
                amount=Decimal("100"),
                amount_usd=Decimal("100"),
                date=datetime.now(),
                start_date=start_date,
                end_date=end_date,
                status=InvoiceStatus.PENDING,
                invoice_number=f"INV-{i}",
                service_delivery=True,
                exchange_rate=exchange_rate,
            )
            for i in range(3)
        ]

        invoice_ids = _bulk_create_and_link_invoices(invoices_chunk)

        # Should return IDs for all invoices
        assert len(invoice_ids) == 3

        # Invoices should be created
        assert PaymentInvoice.objects.filter(id__in=invoice_ids).count() == 3

        # link_invoice_to_completed_works should be called for each invoice
        assert mock_link.call_count == 3


@pytest.mark.django_db
class TestSendAutoInvoiceCreatedNotification:
    @patch("commcare_connect.opportunity.tasks.send_mail")
    def test_send_notification_single_org_single_invoice(self, mock_send_mail):
        """Test notification for single organization with single invoice."""
        user = UserFactory()
        organization = OrganizationFactory()
        OrganizationMembership.objects.create(user=user, organization=organization, is_admin=True)

        opportunity = OpportunityFactory(organization=organization)
        invoice = PaymentInvoiceFactory(opportunity=opportunity, service_delivery=True)

        _send_auto_invoice_created_notification([invoice.id])

        # Should send one email
        assert mock_send_mail.call_count == 1
        call_args = mock_send_mail.call_args
        assert organization.name in call_args.kwargs["subject"]
        assert user.email in call_args.kwargs["recipient_list"]

    @patch("commcare_connect.opportunity.tasks.send_mail")
    def test_send_notification_single_org_multiple_invoices(self, mock_send_mail):
        """Test notification for single organization with multiple invoices."""
        user = UserFactory()
        organization = OrganizationFactory()
        OrganizationMembership.objects.create(user=user, organization=organization, is_admin=True)

        opportunity = OpportunityFactory(organization=organization)
        invoice1 = PaymentInvoiceFactory(opportunity=opportunity, service_delivery=True)
        invoice2 = PaymentInvoiceFactory(opportunity=opportunity, service_delivery=True)

        _send_auto_invoice_created_notification([invoice1.id, invoice2.id])

        # Should send one email with both invoices
        assert mock_send_mail.call_count == 1

    @patch("commcare_connect.opportunity.tasks.send_mail")
    def test_send_notification_multiple_orgs(self, mock_send_mail):
        """Test notification for multiple organizations."""
        user1 = UserFactory()
        organization1 = OrganizationFactory()
        OrganizationMembership.objects.create(user=user1, organization=organization1, is_admin=True)

        user2 = UserFactory()
        organization2 = OrganizationFactory()
        OrganizationMembership.objects.create(user=user2, organization=organization2, is_admin=True)

        opportunity1 = OpportunityFactory(organization=organization1)
        opportunity2 = OpportunityFactory(organization=organization2)

        invoice1 = PaymentInvoiceFactory(opportunity=opportunity1, service_delivery=True)
        invoice2 = PaymentInvoiceFactory(opportunity=opportunity2, service_delivery=True)

        _send_auto_invoice_created_notification([invoice1.id, invoice2.id])

        # Should send two emails (one per organization)
        assert mock_send_mail.call_count == 2

    @patch("commcare_connect.opportunity.tasks.send_mail")
    def test_send_notification_no_recipients(self, mock_send_mail):
        """Test that no notification is sent when organization has no members."""
        organization = OrganizationFactory()
        opportunity = OpportunityFactory(organization=organization)
        invoice = PaymentInvoiceFactory(opportunity=opportunity, service_delivery=True)

        _send_auto_invoice_created_notification([invoice.id])

        # Should not send email
        assert not mock_send_mail.called

    @patch("commcare_connect.opportunity.tasks.send_mail")
    @patch("commcare_connect.opportunity.tasks.logger")
    def test_send_notification_handles_errors(self, mock_logger, mock_send_mail):
        """Test that errors are logged when sending notifications."""
        mock_send_mail.side_effect = Exception("Email error")

        user = UserFactory()
        organization = OrganizationFactory()
        OrganizationMembership.objects.create(user=user, organization=organization, is_admin=True)

        opportunity = OpportunityFactory(organization=organization)
        invoice = PaymentInvoiceFactory(opportunity=opportunity, service_delivery=True)

        _send_auto_invoice_created_notification([invoice.id])

        # Should log error
        assert mock_logger.error.called


@pytest.mark.django_db
class TestGenerateAutomatedServiceDeliveryInvoice:
    @patch("commcare_connect.opportunity.tasks.waffle.switch_is_active")
    def test_task_disabled_when_switch_inactive(self, mock_switch):
        """Test that task does nothing when switch is inactive."""
        mock_switch.return_value = False

        generate_automated_service_delivery_invoice()

        # No invoices should be created
        assert PaymentInvoice.objects.count() == 0

    @patch("commcare_connect.opportunity.tasks.waffle.switch_is_active")
    @patch("commcare_connect.opportunity.tasks.get_start_date_for_invoice")
    @patch("commcare_connect.opportunity.tasks.get_uninvoiced_visit_items")
    @patch("commcare_connect.opportunity.tasks._bulk_create_and_link_invoices")
    @patch("commcare_connect.opportunity.tasks._send_auto_invoice_created_notification")
    def test_task_creates_invoices_for_eligible_opportunities(
        self,
        mock_send_notification,
        mock_bulk_create,
        mock_get_items,
        mock_get_start_date,
        mock_switch,
    ):
        """Test that task creates invoices for eligible opportunities."""
        mock_switch.return_value = True
        mock_get_start_date.return_value = date(2025, 12, 1)
        mock_get_items.return_value = [
            {
                "month": date(2025, 12, 1),
                "payment_unit_name": "Test Unit",
                "number_approved": 10,
                "amount_per_unit": Decimal("10"),
                "total_amount_local": Decimal("100"),
                "total_amount_usd": Decimal("100"),
                "exchange_rate": Decimal("1.0"),
                "currency": "USD",
            }
        ]
        mock_bulk_create.return_value = [1, 2, 3]

        # Create eligible opportunity (managed, active, started after 2026-01-01)
        opportunity = OpportunityFactory(
            active=True,
            managed=True,
            start_date=date(2026, 1, 15),
        )
        ExchangeRateFactory(currency_code="USD", rate=Decimal("1.0"), rate_date=date(2025, 12, 1))

        generate_automated_service_delivery_invoice()

        # Should create and link invoices
        assert mock_bulk_create.called
        assert mock_send_notification.called

    @patch("commcare_connect.opportunity.tasks.waffle.switch_is_active")
    @patch("commcare_connect.opportunity.tasks.get_start_date_for_invoice")
    def test_task_skips_opportunities_with_future_start_date(
        self, mock_get_start_date, mock_switch
    ):
        """Test that opportunities with future start dates are skipped."""
        mock_switch.return_value = True
        # Return a future start date
        future_date = date.today() + timedelta(days=30)
        mock_get_start_date.return_value = future_date

        opportunity = OpportunityFactory(
            active=True,
            managed=True,
            start_date=date(2026, 1, 15),
        )

        generate_automated_service_delivery_invoice()

        # No invoices should be created
        assert PaymentInvoice.objects.count() == 0

    @patch("commcare_connect.opportunity.tasks.waffle.switch_is_active")
    @patch("commcare_connect.opportunity.tasks.get_start_date_for_invoice")
    @patch("commcare_connect.opportunity.tasks.get_uninvoiced_visit_items")
    def test_task_skips_opportunities_with_no_items(
        self, mock_get_items, mock_get_start_date, mock_switch
    ):
        """Test that opportunities with no uninvoiced items are skipped."""
        mock_switch.return_value = True
        mock_get_start_date.return_value = date(2025, 12, 1)
        mock_get_items.return_value = []  # No items

        opportunity = OpportunityFactory(
            active=True,
            managed=True,
            start_date=date(2026, 1, 15),
        )

        generate_automated_service_delivery_invoice()

        # No invoices should be created
        assert PaymentInvoice.objects.count() == 0


@pytest.mark.django_db
class TestInvoiceTasksIntegration:
    """Integration tests for invoice tasks."""

    @patch("commcare_connect.opportunity.tasks.send_mail")
    def test_full_invoice_workflow(self, mock_send_mail):
        """Test complete workflow from invoice creation to notification."""
        user = UserFactory()
        organization = OrganizationFactory()
        OrganizationMembership.objects.create(user=user, organization=organization, is_admin=True)

        opportunity = OpportunityFactory(organization=organization)
        invoice = PaymentInvoiceFactory(
            opportunity=opportunity,
            status=InvoiceStatus.SUBMITTED,
        )

        # Send paid notification
        send_invoice_paid_mail(opportunity.id, [invoice.id])

        # Verify email was sent
        assert mock_send_mail.called