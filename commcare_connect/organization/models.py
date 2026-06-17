from uuid import uuid4

from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from commcare_connect.users.models import User
from commcare_connect.utils.db import BaseModel, slugify_uniquely


def _current_year():
    return timezone.now().year


class PrimarySector(models.TextChoices):
    # TODO: fill in actual sector values before merge
    OTHER = "other", _("Other")


class LLOEntity(models.Model):
    name = models.CharField(max_length=255, unique=True)
    short_name = models.CharField(max_length=40, null=True, blank=True)
    has_used_connect = models.BooleanField(default=False)
    year_of_establishment = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1800), MaxValueValidator(_current_year() + 1)],
    )
    team_size = models.PositiveIntegerField(null=True, blank=True)
    flws_managed = models.PositiveIntegerField(null=True, blank=True)
    countries = models.ManyToManyField("opportunity.Country", blank=True, related_name="llo_entities")
    regions = models.TextField(blank=True)
    primary_sectors = ArrayField(
        models.CharField(max_length=64, choices=PrimarySector.choices),
        default=list,
        blank=True,
    )
    website = models.URLField(blank=True)
    office_address = models.TextField(blank=True)
    contact_emails = models.TextField(blank=True, help_text=_("One email address per line."))
    eoi_links = models.TextField(blank=True, help_text=_("One EOI link per line."))
    notes = models.TextField(blank=True)
    verified = models.BooleanField(default=False)
    members = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        related_name="llo_entities",
        blank=True,
    )

    class Meta:
        verbose_name_plural = "LLO Entities"
        permissions = [
            ("llo_entity_internal_access", "Can access internal LLO Entity list"),
        ]

    def __str__(self):
        if self.short_name:
            return f"{self.name} ({self.short_name})"
        return f"{self.name}"


class Organization(BaseModel):
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True)
    members = models.ManyToManyField(
        settings.AUTH_USER_MODEL, related_name="organizations", through="UserOrganizationMembership"
    )
    program_manager = models.BooleanField(default=False)
    llo_entity = models.ForeignKey(LLOEntity, on_delete=models.SET_NULL, null=True)

    def save(self, *args, **kwargs):
        if not self.id:
            self.slug = slugify_uniquely(self.name, self.__class__)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.slug

    def get_member_emails(self):
        return list(
            self.memberships.exclude(user__email__isnull=True)
            .exclude(user__email="")
            .values_list("user__email", flat=True)
        )


class UserOrganizationMembership(models.Model):
    class Role(models.TextChoices):
        ADMIN = "admin", _("Admin")
        MEMBER = "member", _("Member")
        VIEWER = "viewer", _("Viewer")

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    user = models.ForeignKey(
        User,
        on_delete=models.DO_NOTHING,
        related_name="memberships",
    )
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.MEMBER)
    invite_id = models.CharField(max_length=50, default=uuid4)

    @property
    def is_admin(self):
        return self.role == self.Role.ADMIN

    @property
    def is_viewer(self):
        return self.role == self.Role.VIEWER

    @property
    def is_program_manager(self):
        return self.organization.program_manager and self.is_admin

    class Meta:
        db_table = "organization_membership"
        unique_together = ("user", "organization")
        indexes = [models.Index(fields=["invite_id"])]
