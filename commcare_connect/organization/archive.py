from dataclasses import dataclass
from datetime import date

from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from commcare_connect.opportunity.models import Opportunity
from commcare_connect.organization.models import Organization
from commcare_connect.program.models import Program
from commcare_connect.program.utils import opportunities_linked_to_org


class ArchiveBlockerKind(models.TextChoices):
    PROGRAM = "program", _("Program")
    OPPORTUNITY = "opportunity", _("Opportunity")


@dataclass(frozen=True)
class ArchiveBlocker:
    """A program or opportunity that stops the organization from being archived."""

    name: str
    kind: ArchiveBlockerKind
    # Labels of the parts the organization plays in it.
    roles: list[str]


def archive_blockers(organization: Organization) -> list[ArchiveBlocker]:
    """The live or upcoming programs, then opportunities, that the organization is part of."""
    today = timezone.now().date()
    programs = Program.objects.filter(
        Q(organization=organization) | Q(funder=organization), live_or_upcoming_program_q(today)
    ).order_by("name")
    opportunities = (
        opportunities_linked_to_org(organization)
        .filter(live_or_upcoming_opportunity_q(today))
        .select_related("program")
        .order_by("name")
    )
    return [
        *(
            ArchiveBlocker(program.name, ArchiveBlockerKind.PROGRAM, _org_roles_in_program(organization, program))
            for program in programs
        ),
        *(
            ArchiveBlocker(
                opportunity.name,
                ArchiveBlockerKind.OPPORTUNITY,
                _org_roles_in_opportunity(organization, opportunity),
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
