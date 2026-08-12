import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from functools import cached_property

from django.apps import apps
from django.db import transaction

from commcare_connect.organization.models import Organization
from commcare_connect.program.models import ProgramApplication, ProgramApplicationStatus

logger = logging.getLogger(__name__)


class MergeNotAllowed(Exception):
    """The merge was refused before anything was changed."""


@dataclass(frozen=True)
class OrganizationReassignment:
    app_label: str
    model_name: str
    field_name: str

    @cached_property
    def model(self):
        return apps.get_model(self.app_label, self.model_name)

    @property
    def label(self) -> str:
        return f"{self.model._meta.label}.{self.field_name}"

    def reassign(self, source: Organization, target: Organization) -> int:
        manager = self.model._default_manager
        return manager.filter(**{self.field_name: source}).update(**{self.field_name: target})


# Repointing these before the delete is what clears the two PROTECT relations:
SIMPLE_REASSIGNMENTS: Sequence[OrganizationReassignment] = (
    OrganizationReassignment("opportunity", "CommCareApp", "organization"),
    OrganizationReassignment("opportunity", "Opportunity", "organization"),
    OrganizationReassignment("opportunity", "Opportunity", "supervising_organization"),
    OrganizationReassignment("opportunity", "Payment", "organization"),
    OrganizationReassignment("opportunity", "LabsRecord", "organization"),
    OrganizationReassignment("program", "Program", "organization"),
    OrganizationReassignment("program", "Program", "funder"),
)

# Every relation pointing at Organization. test_merge.py asserts this matches the models, so adding a new foreign
# key to Organization without handling it here fails CI.
HANDLED_RELATIONS = frozenset(
    {
        "flags.Flag.organizations",
        "opportunity.CommCareApp.organization",
        "opportunity.LabsRecord.organization",
        "opportunity.Opportunity.organization",
        "opportunity.Opportunity.supervising_organization",
        "opportunity.Payment.organization",
        "organization.OrganizationInvite.organization",
        "organization.UserOrganizationMembership.organization",
        "program.Program.funder",
        "program.Program.organization",
        "program.Program.watchers",
        "program.ProgramApplication.organization",
    }
)

APPLICATION_STATUS_PRECEDENCE = [
    ProgramApplicationStatus.REJECTED,
    ProgramApplicationStatus.DECLINED,
    ProgramApplicationStatus.INVITED,
    ProgramApplicationStatus.APPLIED,
    ProgramApplicationStatus.ACCEPTED,
]


@dataclass(frozen=True)
class MergeSummary:
    source_slug: str
    target_slug: str
    reassigned: dict[str, int] = field(default_factory=dict)
    programs_watched: int = 0
    applications_moved: int = 0
    applications_deduped: int = 0
    self_applications_removed: int = 0
    memberships_moved: int = 0
    memberships_discarded: int = 0


def merge_organizations(source: Organization, target: Organization) -> MergeSummary:
    """Merge ``source`` into ``target``, then delete ``source``.

    Raises ``MergeNotAllowed`` before any change is made; any other exception rolls the whole merge back.
    """
    _reject_invalid_merge(source, target)

    with transaction.atomic():
        reassigned = _reassign_simple_relations(source, target)
        watched = _move_program_watchers(source, target)
        apps_moved, apps_deduped = _merge_program_applications(source, target)
        # Must follow both the Program.organization reassignment above (which is what
        # makes a program target-owned) and the application merge (which would
        # otherwise move a source application in afterwards and recreate the
        # self-application we just deleted).
        self_apps = _remove_self_applications(target)
        members_moved, members_dropped = _merge_memberships(source, target)

        summary = MergeSummary(
            source_slug=source.slug,
            target_slug=target.slug,
            reassigned=reassigned,
            programs_watched=watched,
            applications_moved=apps_moved,
            applications_deduped=apps_deduped,
            self_applications_removed=self_apps,
            memberships_moved=members_moved,
            memberships_discarded=members_dropped,
        )
        source.delete()

    logger.info("Merged workspace %s into %s: %s", summary.source_slug, summary.target_slug, summary)
    return summary


def _reject_invalid_merge(source: Organization, target: Organization) -> None:
    if source.pk is None or target.pk is None:
        raise MergeNotAllowed("Both organizations must be saved before they can be merged.")
    if source.pk == target.pk:
        raise MergeNotAllowed("An organization cannot be merged into itself.")


def _reassign_simple_relations(source: Organization, target: Organization) -> dict[str, int]:
    reassigned = {}
    for relation in SIMPLE_REASSIGNMENTS:
        count = relation.reassign(source, target)
        reassigned[relation.label] = count
        logger.info("Reassigned %s %s rows from %s to %s", count, relation.label, source.slug, target.slug)
    return reassigned


def _move_program_watchers(source: Organization, target: Organization) -> int:
    """Make the target watch everything the source watched, then drop the source.

    ``add`` is idempotent, so a program both organizations watched needs no explicit deduplication.
    """
    watched = list(source.watched_programs.all())
    target.watched_programs.add(*watched)
    source.watched_programs.clear()
    return len(watched)


def _merge_program_applications(source: Organization, target: Organization) -> tuple[int, int]:
    """Move applications, keeping one row per program at the most advanced status.

    ``(program, organization)`` is not unique in the database but ``invite_organization`` relies on it, calling
    ``update_or_create``, which raises ``MultipleObjectsReturned`` the moment duplicates exist.
    """
    target_applications = {app.program_id: app for app in target.programapplication_set.all()}

    moved = 0
    deduped = 0
    for application in source.programapplication_set.all():
        existing = target_applications.get(application.program_id)
        if existing is None:
            application.organization = target
            application.save(update_fields=["organization"])
            target_applications[application.program_id] = application
            moved += 1
            continue

        # Keep the application that's the most 'advanced'
        surviving_status = _most_advanced_status(existing.status, application.status)
        if surviving_status != existing.status:
            existing.status = surviving_status
            existing.save(update_fields=["status"])
        application.delete()
        deduped += 1

    return moved, deduped


def _most_advanced_status(*statuses: str) -> str:
    return max(statuses, key=APPLICATION_STATUS_PRECEDENCE.index)


def _remove_self_applications(target: Organization) -> int:
    """Drop applications an organization holds to a program it now owns.

    Repointing ``Program.organization`` can leave the target holding an application to its own program, a row
    ``invite_organization`` refuses to create in the first place.
    """
    deleted, _ = ProgramApplication.objects.filter(organization=target, program__organization=target).delete()
    return deleted


def _merge_memberships(source: Organization, target: Organization) -> tuple[int, int]:
    target_user_ids = set(target.memberships.values_list("user_id", flat=True))
    source_memberships = source.memberships.all()

    discarded, _ = source_memberships.filter(user_id__in=target_user_ids).delete()
    # Whatever survived the delete above is target-safe: (user, organization) is unique.
    moved = source_memberships.update(organization=target)
    return moved, discarded
