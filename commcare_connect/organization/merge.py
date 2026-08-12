import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from functools import cached_property

from django.apps import apps
from django.db import transaction

from commcare_connect.organization.models import Organization

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


@dataclass(frozen=True)
class MergeSummary:
    source_slug: str
    target_slug: str
    reassigned: dict[str, int] = field(default_factory=dict)
    programs_watched: int = 0


def merge_organizations(source: Organization, target: Organization) -> MergeSummary:
    """Merge ``source`` into ``target``, then delete ``source``.

    Raises ``MergeNotAllowed`` before any change is made; any other exception rolls the whole merge back.
    """
    _reject_invalid_merge(source, target)

    with transaction.atomic():
        reassigned = _reassign_simple_relations(source, target)
        watched = _move_program_watchers(source, target)

        summary = MergeSummary(
            source_slug=source.slug,
            target_slug=target.slug,
            reassigned=reassigned,
            programs_watched=watched,
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
