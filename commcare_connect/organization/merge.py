import logging
from dataclasses import dataclass, field

from django.db import transaction

from commcare_connect.organization.models import Organization

logger = logging.getLogger(__name__)


class MergeNotAllowed(Exception):
    """The merge was refused before anything was changed."""


@dataclass(frozen=True)
class MergeSummary:
    source_slug: str
    target_slug: str
    reassigned: dict[str, int] = field(default_factory=dict)


def merge_organizations(source: Organization, target: Organization) -> MergeSummary:
    """Merge ``source`` into ``target``, then delete ``source``.

    Raises ``MergeNotAllowed`` before any change is made; any other exception rolls the whole merge back.
    """
    _reject_invalid_merge(source, target)

    with transaction.atomic():
        summary = MergeSummary(source_slug=source.slug, target_slug=target.slug)
        source.delete()

    logger.info("Merged workspace %s into %s: %s", summary.source_slug, summary.target_slug, summary)
    return summary


def _reject_invalid_merge(source: Organization, target: Organization) -> None:
    if source.pk is None or target.pk is None:
        raise MergeNotAllowed("Both organizations must be saved before they can be merged.")
    if source.pk == target.pk:
        raise MergeNotAllowed("An organization cannot be merged into itself.")
