"""Invoices, payments and the finance requests raised for them."""

import datetime
from uuid import uuid4

import pghistory
from django.core.exceptions import ImproperlyConfigured
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils.functional import cached_property
from django.utils.translation import gettext, gettext_lazy

from commcare_connect.opportunity.models.access import OpportunityAccess
from commcare_connect.opportunity.models.base import CHOICE_FIELD_MAX_LENGTH
from commcare_connect.opportunity.models.completed_work import CompletedWork
from commcare_connect.opportunity.models.opportunity import Opportunity
from commcare_connect.opportunity.models.payment_units import PaymentUnit
from commcare_connect.opportunity.models.reference import ExchangeRate
from commcare_connect.organization.models import Organization
from commcare_connect.users.models import User
from commcare_connect.utils.db import BaseModel


class InvoiceStatus(models.TextChoices):
    PENDING_NM_REVIEW = "pending_nm_review", gettext("Pending Network Manager Review")
    PENDING_PM_REVIEW = "pending_pm_review", gettext("Pending Program Manager Review")
    CANCELLED_BY_NM = "cancelled_by_nm", gettext("Cancelled by Network Manager")
    READY_TO_PAY = "ready_to_pay", gettext("Ready to Pay")
    REJECTED_BY_PM = "rejected_by_pm", gettext("Rejected by Program Manager")
    PAID = "paid", gettext("Paid")
    ARCHIVED = "archived", gettext("Archived")

    @classmethod
    def get_label(cls, status):
        return cls(status).label

    @classmethod
    def get_choices(cls):
        return cls.choices


class BillToAddress(BaseModel):
    """An entity an invoice can be billed to."""

    name = models.CharField(max_length=255)
    address = models.TextField()
    is_active = models.BooleanField(default=True)
    is_default = models.BooleanField(default=False)

    class Meta:
        verbose_name_plural = gettext_lazy("Bill to Addresses")
        constraints = [
            models.UniqueConstraint(
                fields=["is_default"], condition=Q(is_default=True), name="unique_default_bill_to_address"
            ),
            models.CheckConstraint(
                condition=Q(is_active=True) | Q(is_default=False), name="default_bill_to_address_is_active"
            ),
        ]

    def __str__(self):
        return self.name

    @classmethod
    def get_default(cls):
        try:
            return cls.objects.get(is_default=True)
        except cls.DoesNotExist:
            raise ImproperlyConfigured("No default Bill To address is configured.")


@pghistory.track(fields=["status"])
class PaymentInvoice(models.Model):
    class InvoiceType(models.TextChoices):
        service_delivery = "service_delivery", gettext("Service Delivery")
        custom = "custom", gettext("Custom")

    payment_invoice_id = models.UUIDField(editable=False, default=uuid4, unique=True)
    opportunity = models.ForeignKey(Opportunity, on_delete=models.CASCADE)
    amount = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0)])
    amount_usd = models.DecimalField(max_digits=10, decimal_places=2, null=True)
    date = models.DateField()
    invoice_number = models.CharField(max_length=50)
    service_delivery = models.BooleanField(default=True)
    exchange_rate = models.ForeignKey(ExchangeRate, on_delete=models.DO_NOTHING, null=True)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    title = models.CharField(max_length=255, null=True, blank=True)
    description = models.TextField(null=True, blank=True)
    date_of_expense = models.DateField(null=True, blank=True)
    status = models.CharField(
        choices=InvoiceStatus, default=InvoiceStatus.PENDING_NM_REVIEW, max_length=CHOICE_FIELD_MAX_LENGTH
    )
    archived_date = models.DateTimeField(null=True, blank=True)
    invoice_ticket_link = models.URLField(null=True, blank=True)

    class Meta:
        unique_together = ("opportunity", "invoice_number")

    @property
    def invoice_type(self):
        if self.service_delivery:
            return PaymentInvoice.InvoiceType.service_delivery
        return PaymentInvoice.InvoiceType.custom

    @cached_property
    def is_paid(self):
        return Payment.objects.filter(invoice=self).exists()

    def get_status_display(self):
        return InvoiceStatus.get_label(self.status)

    @cached_property
    def nm_certification(self):
        """The Network Manager's certification: who submitted the invoice for PM review, and when."""
        return self._certification_for(InvoiceStatus.PENDING_PM_REVIEW)

    @cached_property
    def pm_certification(self):
        """The Program Manager's certification: who approved the invoice for payment, and when."""
        return self._certification_for(InvoiceStatus.READY_TO_PAY)

    def _certification_for(self, status):
        event = (
            self.status_events.filter(status=status)
            .select_related("pgh_context")
            .order_by("-pgh_created_at", "-pgh_id")
            .first()
        )
        if not event or not event.pgh_context:
            return None
        return {"name": self._certifier_name(event), "certified_at": event.pgh_created_at}

    @staticmethod
    def _certifier_name(event):
        email = event.pgh_context.metadata.get("user_email")
        user = User.objects.filter(email=email).first()
        return f"{user.name} ({email})" if user and user.name else email


class InvoiceFinanceRequestStatus(models.TextChoices):
    PENDING = "pending", gettext_lazy("Submitting")
    SUBMITTED = "submitted", gettext_lazy("Submitted")
    FAILED = "failed", gettext_lazy("Failed")


class InvoiceFinanceRequest(BaseModel):
    """The payment request raised with Finance for one invoice: a ticket on the Project Finance service desk.

    The answers are kept so a failed submission can be retried, and so the opportunity's next request can be
    prefilled. `contracted_entity` and `gl_account` hold the Jira Form's choice IDs. Their labels live in
    `utils/finance_request.py`, so a change to Finance's form needs no migration.
    """

    invoice = models.OneToOneField(PaymentInvoice, on_delete=models.CASCADE, related_name="finance_request")
    status = models.CharField(
        choices=InvoiceFinanceRequestStatus,
        default=InvoiceFinanceRequestStatus.PENDING,
        max_length=CHOICE_FIELD_MAX_LENGTH,
    )
    submitted_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    contracted_entity = models.CharField(max_length=10)
    gl_account = models.CharField(max_length=10)
    project_name = models.CharField(max_length=255, blank=True)
    new_vendor = models.BooleanField()
    issue_key = models.CharField(max_length=50, blank=True)
    error = models.TextField(blank=True)


class Payment(models.Model):
    payment_id = models.UUIDField(editable=False, default=uuid4, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0)])
    amount_usd = models.DecimalField(max_digits=10, decimal_places=2, null=True)
    date_paid = models.DateTimeField(default=datetime.datetime.utcnow)
    # This is used to indicate payments made to Opportunity Users
    opportunity_access = models.ForeignKey(OpportunityAccess, on_delete=models.DO_NOTHING, null=True, blank=True)
    payment_unit = models.ForeignKey(
        PaymentUnit,
        on_delete=models.CASCADE,
        related_name="payments",
        related_query_name="payment",
        null=True,
    )
    confirmed = models.BooleanField(default=False)
    confirmation_date = models.DateTimeField(null=True)
    # This is used to indicate Payments made to Network Manager organizations
    organization = models.ForeignKey(Organization, on_delete=models.DO_NOTHING, null=True, blank=True)
    invoice = models.OneToOneField(PaymentInvoice, on_delete=models.DO_NOTHING, null=True, blank=True)
    payment_method = models.CharField(max_length=50, null=True, blank=True)
    payment_operator = models.CharField(max_length=50, null=True, blank=True)


class CompletedWorkInvoice(BaseModel):
    """Frozen per-work billing record: how much of one CompletedWork was billed on one PaymentInvoice.

    One row per (invoice, completed_work). Snapshotted at invoice creation (and backfilled for legacy
    invoices) so invoice line items are read from frozen rows instead of recomputed from
    CompletedWork.saved_* on every view. Stores both FLW pay and org pay.
    """

    invoice = models.ForeignKey(
        PaymentInvoice,
        on_delete=models.PROTECT,
        related_name="work_items",
    )
    completed_work = models.ForeignKey(
        CompletedWork,
        on_delete=models.PROTECT,
        related_name="invoice_items",
    )
    month = models.DateField(
        help_text=gettext_lazy("First of the month the billed units are attributed to (period attribution).")
    )
    billed_count = models.PositiveIntegerField(
        validators=[MinValueValidator(1)],
        help_text=gettext_lazy("Approved units billed on THIS invoice (delta)."),
    )
    flw_amount_local = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    flw_amount_usd = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    org_amount_local = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    org_amount_usd = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    exchange_rate = models.ForeignKey(
        ExchangeRate,
        on_delete=models.PROTECT,
        related_name="completed_work_invoices",
        help_text=gettext_lazy("ExchangeRate row used for this row's month."),
    )
    # True when this billing represents additional approved work
    # obtained after the initial billing.
    is_delta = models.BooleanField(default=False)

    class Meta:
        unique_together = ("invoice", "completed_work")
