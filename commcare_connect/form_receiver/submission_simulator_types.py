"""Value types for the local Test submissions page.

Kept apart from ``submission_simulator`` so ``opportunity.forms`` can use them without importing the
form processor, which every process would otherwise load through the admin.
"""

from dataclasses import dataclass
from datetime import datetime

from django.db import models
from django.utils.translation import gettext_lazy as _


class FormType(models.TextChoices):
    DELIVER = "deliver", _("Deliver")
    LEARN = "learn", _("Learn module")
    ASSESSMENT = "assessment", _("Assessment")


class WorkerStep(models.TextChoices):
    """A worker's steps before delivering, in the order production takes them."""

    ACCEPT = "accept", _("Accept invite")
    LEARN = "learn", _("Start learning")
    CLAIM = "claim", _("Claim opportunity")
    LIMITS = "limits", _("Create claim limits")


@dataclass(frozen=True)
class SubmissionOverrides:
    """What a person can change about a simulated form. ``None`` means "generate it"."""

    entity_id: str | None = None
    entity_name: str | None = None
    visit_time: datetime | None = None
    latitude: float | None = None
    longitude: float | None = None
    deliver_block_count: int = 1
