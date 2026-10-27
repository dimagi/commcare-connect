from dataclasses import dataclass
from datetime import date

from django.db import models, transaction
from django.db.models import BooleanField, DecimalField, Exists, ExpressionWrapper, OuterRef, Q, QuerySet
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from commcare_connect.opportunity.helpers import total_accrued_sq, total_paid_sq
from commcare_connect.opportunity.models import (
    InvoiceStatus,
    Opportunity,
    PaymentInvoice,
    UserVisit,
    VisitReviewStatus,
    VisitValidationStatus,
)
from commcare_connect.organization.models import Organization
from commcare_connect.program.models import Program
from commcare_connect.program.utils import opportunities_linked_to_org

LIVE_OR_UPCOMING = _("Live or upcoming")

# Every other status counts as unsettled, so a status added later blocks archiving until it is listed here.
SETTLED_INVOICE_STATUSES = (
    InvoiceStatus.PAID,
    InvoiceStatus.CANCELLED_BY_NM,
    InvoiceStatus.REJECTED_BY_PM,
    InvoiceStatus.ARCHIVED,
)


class ArchiveBlockerKind(models.TextChoices):
    PROGRAM = "program", _("Program")
    OPPORTUNITY = "opportunity", _("Opportunity")


@dataclass(frozen=True)
class ArchiveBlocker:
    """A program or opportunity that stops the organization from being archived, and why."""

    name: str
    kind: ArchiveBlockerKind
    # Labels of the parts the organization plays in it.
    roles: list[str]
    # Labels of what still has to finish or be settled.
    reasons: list[str]


def archive_blockers(organization: Organization) -> list[ArchiveBlocker]:
    """The programs, then opportunities, the organization is part of that are live or upcoming or unsettled."""
    today = timezone.now().date()
    programs = Program.objects.filter(
        Q(organization=organization) | Q(funder=organization), live_or_upcoming_program_q(today)
    ).order_by("name")
    opportunities = (
        _annotate_block_reasons(opportunities_linked_to_org(organization), today)
        .filter(
            Q(is_live_or_upcoming=True)
            | Q(has_unsettled_invoices=True)
            | Q(payments_due__gt=0)
            | Q(has_visits_awaiting_review=True)
        )
        .select_related("program")
        .order_by("name")
    )
    return [
        *(
            ArchiveBlocker(
                program.name,
                ArchiveBlockerKind.PROGRAM,
                _org_roles_in_program(organization, program),
                [LIVE_OR_UPCOMING],
            )
            for program in programs
        ),
        *(
            ArchiveBlocker(
                opportunity.name,
                ArchiveBlockerKind.OPPORTUNITY,
                _org_roles_in_opportunity(organization, opportunity),
                _block_reasons(opportunity),
            )
            for opportunity in opportunities
        ),
    ]


@transaction.atomic
def archive_organization(organization: Organization, *, user_email: str, reason: str) -> None:
    """Archive the organization together with its contacts, noting the reason when one is given."""
    organization.contacts.filter(is_archived=False).update(
        is_archived=True, modified_by=user_email, date_modified=timezone.now()
    )
    organization.is_archived = True
    organization.modified_by = user_email
    _append_system_note(organization, "Archived", user_email, reason)
    organization.save(update_fields=["is_archived", "notes", "modified_by", "date_modified"])


def live_or_upcoming_program_q(today: date) -> Q:
    """Programs that haven't ended. Every program has both dates, so this covers upcoming ones too."""
    return Q(end_date__gte=today)


def live_or_upcoming_opportunity_q(today: date) -> Q:
    """Opportunities that aren't archived and haven't ended, or have no end date yet and are still switched on.

    A future end date counts whether or not the opportunity is switched on or a test.
    """
    return Q(archived=False) & (Q(end_date__gte=today) | Q(end_date__isnull=True, active=True))


def _append_system_note(organization: Organization, action: str, user_email: str, reason: str) -> None:
    reason = reason.strip()
    if not reason:
        return
    note = f"[System Note] {action} on {timezone.now().date():%Y-%m-%d} by {user_email}: {reason}"
    organization.notes = f"{organization.notes}\n{note}" if organization.notes else note


def _annotate_block_reasons(opportunities: QuerySet[Opportunity], today: date) -> QuerySet[Opportunity]:
    # Visits on opportunities that verify them automatically are left out, as on the program homes'
    # "pending review" panels.
    visits_awaiting_review = UserVisit.objects.filter(
        Q(status=VisitValidationStatus.pending)
        | Q(status=VisitValidationStatus.approved, review_status=VisitReviewStatus.pending),
        opportunity=OuterRef("pk"),
        opportunity__automatic_visit_verification=False,
    )
    return opportunities.annotate(
        is_live_or_upcoming=ExpressionWrapper(live_or_upcoming_opportunity_q(today), output_field=BooleanField()),
        has_unsettled_invoices=Exists(
            PaymentInvoice.objects.filter(opportunity=OuterRef("pk")).exclude(status__in=SETTLED_INVOICE_STATUSES)
        ),
        payments_due=ExpressionWrapper(total_accrued_sq() - total_paid_sq(), output_field=DecimalField()),
        has_visits_awaiting_review=Exists(visits_awaiting_review),
    )


def _block_reasons(opportunity: Opportunity) -> list[str]:
    reasons = []
    if opportunity.is_live_or_upcoming:
        reasons.append(LIVE_OR_UPCOMING)
    if opportunity.has_unsettled_invoices:
        reasons.append(_("Unsettled invoices"))
    if opportunity.payments_due > 0:
        reasons.append(_("Payments owed to workers"))
    if opportunity.has_visits_awaiting_review:
        reasons.append(_("Visits awaiting review"))
    return reasons


def _org_roles_in_program(organization: Organization, program: Program) -> list[str]:
    roles = []
    if program.organization_id == organization.pk:
        roles.append(_("Program owner"))
    if program.funder_id == organization.pk:
        roles.append(_("Program funder"))
    return roles


def _org_roles_in_opportunity(organization: Organization, opportunity: Opportunity) -> list[str]:
    # The supervising organization defaults to the program's owner, so an organization often holds
    # more than one of these roles.
    roles = _org_roles_in_program(organization, opportunity.program)
    if opportunity.organization_id == organization.pk:
        roles.append(_("Network manager"))
    if opportunity.supervising_organization_id == organization.pk:
        roles.append(_("Supervising organization"))
    return roles
