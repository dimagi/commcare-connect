from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from commcare_connect.organization.models import Organization, OrganizationStatus
from commcare_connect.program.models import Program, ProgramApplicationStatus


class ArchiveNotAllowed(Exception):
    """The organization was not archived, and nothing was changed."""


def archive_organization(organization: Organization) -> None:
    """Sets the organization's status to Archived, refusing while it has a live program."""
    programs = list(live_programs(organization).values_list("name", flat=True))
    if programs:
        raise ArchiveNotAllowed(
            f"{organization.name} runs, funds or delivers in programs that haven't ended: {', '.join(programs)}."
        )
    organization.status = OrganizationStatus.ARCHIVED
    organization.save(update_fields=["status", "date_modified"])


def live_programs(organization):
    """Programs that haven't ended which the organization runs, funds or has an accepted application to."""
    return _unended_programs().filter(_involving(organization)).distinct().order_by("name")


def has_live_program():
    """An annotation expression: whether the annotated organization has any live program."""
    return Exists(_unended_programs().filter(_involving(OuterRef("pk"))))


def _unended_programs():
    return Program.objects.filter(end_date__gte=timezone.now().date())


def _involving(organization):
    return (
        Q(organization=organization)
        | Q(funder=organization)
        | Q(
            programapplication__organization=organization, programapplication__status=ProgramApplicationStatus.ACCEPTED
        )
    )
