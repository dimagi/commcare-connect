"""Seed approved invoices for trying the "Send to Finance" flow locally.

Makes the given user an admin of a program manager organization whose program runs a managed
opportunity for a separate network manager organization, then adds invoices the program manager
has approved (Ready to Pay). Rerunning reuses the organizations, program and opportunity and adds
more invoices. Pair it with JIRA_SERVICE_DESK_DRY_RUN so nothing reaches Jira.
"""

import datetime
from decimal import Decimal

import pghistory
from dateutil.relativedelta import relativedelta
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.urls import reverse
from django.utils.timezone import now

from commcare_connect.opportunity.models import Currency, InvoiceStatus, Opportunity
from commcare_connect.opportunity.tests.factories import HQApiKeyFactory, OpportunityFactory, PaymentInvoiceFactory
from commcare_connect.organization.models import Organization, UserOrganizationMembership
from commcare_connect.program.models import Program
from commcare_connect.program.tests.factories import ProgramFactory
from commcare_connect.users.models import User

PM_ORG_NAME = "Finance Request Test PM"
NM_ORG_NAME = "Finance Request Test LLO"
PROGRAM_NAME = "Finance Request Test Program"
OPPORTUNITY_NAME = "Finance Request Test Opportunity"


class Command(BaseCommand):
    help = "Create Ready to Pay invoices on a managed opportunity for trying the Send to Finance flow locally."

    def add_arguments(self, parser):
        parser.add_argument("email", help="Email of an existing user, who becomes the program manager.")
        parser.add_argument("--invoices", type=int, default=3, help="Number of invoices to add (default 3).")

    def handle(self, email, invoices, **options):
        if not settings.DEBUG:
            raise CommandError("This command creates test data and only runs with DEBUG on.")
        user = User.objects.filter(email=email).first()
        if user is None:
            raise CommandError(f"No user with email {email}.")

        with transaction.atomic():
            pm_org = self._organization(PM_ORG_NAME, program_manager=True)
            UserOrganizationMembership.objects.get_or_create(
                organization=pm_org, user=user, defaults={"role": UserOrganizationMembership.Role.ADMIN}
            )
            opportunity = self._opportunity(pm_org, self._organization(NM_ORG_NAME, program_manager=False), user)
            created = [self._approved_invoice(opportunity, user, index) for index in range(invoices)]

        self.stdout.write(f"Log in as {email} and open:")
        for invoice in created:
            path = reverse(
                "opportunity:invoice_review",
                args=(pm_org.slug, opportunity.opportunity_id, invoice.payment_invoice_id),
            )
            self.stdout.write(f"  http://localhost:8000{path}")

    def _organization(self, name, program_manager):
        organization = Organization.objects.filter(name=name).first()
        if organization is None:
            organization = Organization.objects.create(name=name, program_manager=program_manager)
        return organization

    def _opportunity(self, pm_org, nm_org, user):
        opportunity = Opportunity.objects.filter(name=OPPORTUNITY_NAME, organization=nm_org).first()
        if opportunity:
            return opportunity
        program = Program.objects.filter(name=PROGRAM_NAME, organization=pm_org).first() or ProgramFactory(
            name=PROGRAM_NAME, organization=pm_org
        )
        return OpportunityFactory(
            name=OPPORTUNITY_NAME,
            organization=nm_org,
            program=program,
            managed=True,
            # The factory's default API key creates a user with a sequential username, which
            # collides with users left by earlier runs.
            api_key=HQApiKeyFactory(user=user),
            currency=Currency.objects.get(code="KES"),
        )

    def _approved_invoice(self, opportunity, user, index):
        month_start = now().date().replace(day=1) - relativedelta(months=index + 1)
        invoice = PaymentInvoiceFactory(
            opportunity=opportunity,
            invoice_number=f"FRT-{now():%Y%m%d%H%M%S}-{index + 1}",
            service_delivery=False,
            title="Community health worker training",
            description=f"Training and supervision, {month_start:%B %Y}",
            amount=Decimal("125000.00"),
            amount_usd=Decimal("968.50"),
            date=now().date(),
            start_date=month_start,
            end_date=month_start + relativedelta(months=1) - datetime.timedelta(days=1),
            date_of_expense=month_start,
            status=InvoiceStatus.PENDING_PM_REVIEW,
        )
        # Approve inside a history context, as a request would, so the invoice records who approved it.
        with pghistory.context(username=user.username, user_email=user.email):
            invoice.status = InvoiceStatus.READY_TO_PAY
            invoice.save(update_fields=["status"])
        return invoice
