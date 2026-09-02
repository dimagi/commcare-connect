import secrets
from datetime import timedelta

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from commcare_connect.users.models import User
from commcare_connect.utils.db import BaseModel, slugify_uniquely
from commcare_connect.utils.permission_const import WORKSPACE_ENTITY_MANAGEMENT_ACCESS


class LLOEntity(models.Model):
    name = models.CharField(max_length=255, unique=True)
    short_name = models.CharField(max_length=40, null=True, blank=True)

    class Meta:
        verbose_name_plural = "LLO Entities"

    def __str__(self):
        if self.short_name:
            return f"{self.name} ({self.short_name})"
        return f"{self.name}"

    @classmethod
    def visible_to(cls, user):
        if user.has_perm(WORKSPACE_ENTITY_MANAGEMENT_ACCESS):
            return cls.objects.all()
        return cls.objects.filter(organization__memberships__user=user).distinct()


def _current_year():
    return timezone.now().year


class PrimarySector(models.Model):
    name = models.CharField(max_length=255)
    slug = models.CharField(max_length=255)
    description = models.CharField(max_length=255)

    def __str__(self):
        return self.name


class Organization(BaseModel):
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True)
    members = models.ManyToManyField(
        settings.AUTH_USER_MODEL, related_name="organizations", through="UserOrganizationMembership"
    )
    program_manager = models.BooleanField(default=False)
    funder = models.BooleanField(default=False)
    llo_entity = models.ForeignKey(LLOEntity, on_delete=models.SET_NULL, null=True)
    short_name = models.CharField(max_length=40, null=True, blank=True)
    has_used_connect = models.BooleanField(default=False)
    year_of_establishment = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1800), MaxValueValidator(_current_year() + 1)],
    )
    team_size = models.PositiveIntegerField(null=True, blank=True)
    flws_managed = models.PositiveIntegerField(null=True, blank=True)
    countries = models.ManyToManyField("opportunity.Country", blank=True, related_name="organizations")
    regions = models.TextField(blank=True)
    primary_sectors = models.ManyToManyField(PrimarySector, null=True, blank=True)
    website = models.URLField(blank=True)
    office_address = models.TextField(blank=True)
    contact_emails = models.TextField(blank=True, help_text=_("One email address per line."))
    eoi_links = models.TextField(blank=True, help_text=_("One EOI link per line."))
    notes = models.TextField(blank=True)
    verified = models.BooleanField(default=False)

    def save(self, *args, **kwargs):
        if not self.id:
            self.slug = slugify_uniquely(self.name, self.__class__)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.slug

    @classmethod
    def visible_to(cls, user):
        if user.has_perm(WORKSPACE_ENTITY_MANAGEMENT_ACCESS):
            return cls.objects.all()
        return cls.objects.filter(memberships__user=user)

    def get_member_emails(self, exclude_viewer=False):
        member_query = self.memberships.exclude(user__email__isnull=True).exclude(user__email="")

        if exclude_viewer:
            member_query = member_query.exclude(role=UserOrganizationMembership.Role.VIEWER)

        return list(member_query.values_list("user__email", flat=True))


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

    @property
    def is_admin(self):
        return self.role == self.Role.ADMIN

    @property
    def is_viewer(self):
        return self.role == self.Role.VIEWER

    class Meta:
        db_table = "organization_membership"
        unique_together = ("user", "organization")


class OrganizationInvite(BaseModel):
    EXPIRY_DAYS = 7
    REINVITE_COOLDOWN = timedelta(minutes=5)

    class Status(models.TextChoices):
        INVITED = "invited", _("Invited")
        ACCEPTED = "accepted", _("Accepted")
        REVOKED = "revoked", _("Revoked")

    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="invites")
    email = models.EmailField()
    role = models.CharField(max_length=20, choices=UserOrganizationMembership.Role.choices)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.INVITED)
    token = models.CharField(max_length=64, unique=True, default=secrets.token_urlsafe)
    invited_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name="sent_invites")

    class Meta:
        unique_together = ("organization", "email")

    def __str__(self):
        return f"Invite for {self.email} to {self.organization}"

    @property
    def expiry_date(self):
        """Re-inviting bumps date_modified, which restarts the window."""
        return self.date_modified + timedelta(days=self.EXPIRY_DAYS)

    @property
    def is_in_reinvite_cooldown(self):
        """Throttles reinvites so the invite mail cannot be used to hammer an address.

        Only a pending invite can be reinvited — reinviting a revoked or accepted address
        is a fresh decision rather than a retry, so the window does not apply to it.
        """
        return self.status == self.Status.INVITED and timezone.now() < self.date_modified + self.REINVITE_COOLDOWN

    @property
    def is_expired(self):
        return self.status == self.Status.INVITED and timezone.now() > self.expiry_date

    @classmethod
    def send_invite(cls, organization, email, role, invited_by):
        """Creates a pending invite, or refreshes any existing one for this address.

        An existing invite is reset to pending whatever state it was in — revoked, accepted,
        lapsed, or still pending, which is the reinvite case.
        """
        existing = cls.objects.filter(organization=organization, email=email).first()
        if existing and existing.is_in_reinvite_cooldown:
            return None

        invite, created = cls.objects.update_or_create(
            organization=organization,
            email=email,
            defaults={
                "role": role,
                "status": cls.Status.INVITED,
                "token": secrets.token_urlsafe(),
                "invited_by": invited_by,
                "modified_by": invited_by.email,
            },
        )
        if created:
            invite.created_by = invited_by.email
            invite.save(update_fields=["created_by"])
        return invite

    def accept(self, user):
        membership, _created = UserOrganizationMembership.objects.update_or_create(
            organization=self.organization, user=user, defaults={"role": self.role}
        )
        self.status = self.Status.ACCEPTED
        self.modified_by = user.email
        self.save(update_fields=["status", "modified_by", "date_modified"])
        return membership
