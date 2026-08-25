import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from functools import cached_property

from django.apps import apps
from django.db import transaction
from django.db.models import Q

from commcare_connect.opportunity.models import Opportunity
from commcare_connect.organization.models import Organization, OrganizationInvite
from commcare_connect.program.models import APPLICATION_STATUS_PRECEDENCE, ProgramApplication
from commcare_connect.program.utils import clear_managed_opp_cache

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
    applications_moved: int = 0
    applications_deduped: int = 0
    self_applications_removed: int = 0
    memberships_moved: int = 0
    memberships_discarded: int = 0
    invites_moved: int = 0
    invites_discarded: int = 0
    flags_cleared: int = 0


def merge_organizations(source: Organization, target: Organization) -> MergeSummary:
    """Merge ``source`` into ``target``, then delete ``source``.

    Raises ``MergeNotAllowed`` before any change is made; any other exception rolls the whole merge back.
    """
    _reject_invalid_merge(source, target)
    stale_opportunities = _opportunities_cached_against(source)

    with transaction.atomic():
        reassigned = _reassign_simple_relations(source, target)
        watched = _move_program_watchers(source, target)
        apps_moved, apps_deduped = _merge_program_applications(source, target)
        # Ordering below matters. Self-applications: after Program.organization is repointed (which is what makes a
        # program target-owned) and after the application merge (which would otherwise move a source application in
        # and recreate the row). Invites: after memberships, so the "already a member" check sees the moved rows.
        # Flags: before the delete, because m2m_changed is what flushes waffle's cache.
        self_apps = _remove_self_applications(target)
        members_moved, members_dropped = _merge_memberships(source, target)
        invites_moved, invites_dropped = _move_pending_invites(source, target)
        flags_cleared = _clear_flag_memberships(source)

        transaction.on_commit(lambda: _clear_opportunity_caches(stale_opportunities))

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
            invites_moved=invites_moved,
            invites_discarded=invites_dropped,
            flags_cleared=flags_cleared,
        )
        source.delete()

    logger.info("Merged workspace %s into %s: %s", summary.source_slug, summary.target_slug, summary)
    return summary


def _reject_invalid_merge(source: Organization, target: Organization) -> None:
    if source.pk is None or target.pk is None:
        raise MergeNotAllowed("Both organizations must be saved before they can be merged.")
    if source.pk == target.pk:
        raise MergeNotAllowed("An organization cannot be merged into itself.")
    _reject_shared_commcare_apps(source, target)


def _reject_shared_commcare_apps(source: Organization, target: Organization) -> None:
    """Refuse a merge that would leave the target with two rows for one HQ app."""
    app_key = ("cc_app_id", "cc_domain", "hq_server_id")
    shared = set(source.apps.values_list(*app_key)) & set(target.apps.values_list(*app_key))
    if shared:
        conflicts = sorted({f"{cc_domain}/{cc_app_id}" for cc_app_id, cc_domain, _ in shared})
        raise MergeNotAllowed(
            f"Both workspaces are connected to the same CommCare app(s): {', '.join(conflicts)}. "
            "Merging would leave the surviving workspace with duplicates that break "
            "opportunity creation. Remove the redundant app from one workspace first."
        )


def _opportunities_cached_against(source: Organization) -> list[Opportunity]:
    """Opportunities whose ``get_managed_opp`` entry holds a reference to the source.

    That cache stores the opportunity with its program and the program's organization attached, so a merge
    invalidates it.
    """
    return list(
        Opportunity.objects.filter(
            Q(organization=source)
            | Q(supervising_organization=source)
            | Q(program__organization=source)
            | Q(program__funder=source)
        ).only("id", "opportunity_id")
    )


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
    """Move applications, keeping one row per program at the most advanced status."""
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


def _move_pending_invites(source: Organization, target: Organization) -> tuple[int, int]:
    """Move the live invites the target has no equivalent for"""
    taken_emails = {email.lower() for email in target.invites.values_list("email", flat=True)}
    taken_emails |= {email.lower() for email in target.memberships.values_list("user__email", flat=True) if email}

    moving = []
    discarded = 0
    for invite in source.invites.all():
        is_live = invite.status == OrganizationInvite.Status.INVITED and not invite.is_expired
        if not is_live or invite.email.lower() in taken_emails:
            discarded += 1
            continue

        moving.append(invite.pk)
        taken_emails.add(invite.email.lower())

    OrganizationInvite.objects.filter(pk__in=moving).update(organization=target)
    return len(moving), discarded


def _clear_flag_memberships(source: Organization) -> int:
    """Remove the source from its feature flags before it is deleted so the flag's cache is cleared."""
    flags = list(source.flag_set.all())
    for flag in flags:
        flag.organizations.remove(source)
    return len(flags)


def _clear_opportunity_caches(opportunities: Sequence[Opportunity]) -> None:
    for opportunity in opportunities:
        clear_managed_opp_cache(opportunity)


def relation_counts(organization: Organization) -> dict[str, int]:
    counts = {}
    for label in HANDLED_RELATIONS:
        app_label, model_name, field_name = label.split(".")
        model = apps.get_model(app_label, model_name)
        counts[label] = model._default_manager.filter(**{field_name: organization}).count()
    return counts


def programs_hidden_by_merge(source: Organization, target: Organization) -> list[str]:
    """Names of the source's programs the target would own but not be able to show.

    ``Program.organization`` is repointed like any other relation, but the program views sit behind
    ``org_pm_required``, so a target that is not a program manager inherits the rows and hides them. Nothing is
    lost: ticking "Program manager" on the survivor brings them all back.
    """
    if target.program_manager:
        return []
    return sorted(source.program_set.values_list("name", flat=True))
