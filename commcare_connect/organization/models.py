import secrets
from datetime import timedelta
from functools import partial
from uuid import uuid4

import pghistory
from django.conf import settings
from django.db import models, transaction
from django.db.models.functions import Lower
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from commcare_connect.users.models import User
from commcare_connect.utils.db import BaseModel, slugify_uniquely
from commcare_connect.utils.permission_const import WORKSPACE_ENTITY_MANAGEMENT_ACCESS


class PrimarySector(models.Model):
    name = models.CharField(max_length=255)
    slug = models.CharField(max_length=255)
    description = models.CharField(max_length=255)

    def __str__(self):
        return self.name


class TeamSizeRange(models.TextChoices):
    """Team size is collected as a bracket rather than a headcount — nobody reports an exact number."""

    XS = "1-10", "1-10"
    S = "11-50", "11-50"
    M = "51-200", "51-200"
    L = "201-500", "201-500"
    XL = "500+", "500+"


class OrganizationStatus(models.TextChoices):
    ACTIVE = "active", _("Active")
    INACTIVE = "inactive", _("Inactive")
    PROSPECTIVE = "prospective", _("Prospective")


ORGANIZATION_STATUS_DEFINITIONS = {
    OrganizationStatus.ACTIVE: _("1+ open contracts"),
    OrganizationStatus.INACTIVE: _("0 open contracts, and 1+ previously active contracts"),
    OrganizationStatus.PROSPECTIVE: _("0 open contracts, 0 previously active contracts, 1 active contact"),
}


# Every field except BaseModel's audit fields, which the event row already records as its own
# timestamp and pghistory context.
UNTRACKED_MODEL_FIELDS = ["created_by", "modified_by", "date_created", "date_modified"]


@pghistory.track(
    pghistory.InsertEvent(), pghistory.UpdateEvent(), pghistory.DeleteEvent(), exclude=UNTRACKED_MODEL_FIELDS
)
class Organization(BaseModel):
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True)
    members = models.ManyToManyField(
        settings.AUTH_USER_MODEL, related_name="organizations", through="UserOrganizationMembership"
    )
    program_manager = models.BooleanField(default=False)
    funder = models.BooleanField(default=False)
    short_name = models.CharField(max_length=40, null=True, blank=True)
    has_used_connect = models.BooleanField(default=False)
    year_of_establishment = models.PositiveSmallIntegerField(null=True, blank=True)
    team_size = models.CharField(max_length=20, choices=TeamSizeRange, blank=True)
    flws_managed = models.PositiveIntegerField(null=True, blank=True)
    countries = models.ManyToManyField(
        "opportunity.Country", blank=True, related_name="organizations", through="OrganizationCountry"
    )
    regions = models.TextField(blank=True)
    primary_sectors = models.ManyToManyField(PrimarySector, null=True, blank=True, through="OrganizationPrimarySector")
    website = models.URLField(blank=True)
    office_address = models.TextField(blank=True)
    contact_emails = models.TextField(blank=True, help_text=_("One email address per line."))
    eoi_links = models.TextField(blank=True, help_text=_("One EOI link per line."))
    notes = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=OrganizationStatus, blank=True)
    latest_msa_link = models.URLField(blank=True)
    latest_work_order_link = models.URLField(blank=True)
    verified = models.BooleanField(default=False)
    is_test = models.BooleanField(default=False)

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

    def get_member_emails(self, exclude_viewer=False, role=None):
        member_query = self.memberships.exclude(user__email__isnull=True).exclude(user__email="")

        if role:
            member_query = member_query.filter(role=role)

        if exclude_viewer:
            member_query = member_query.exclude(role=UserOrganizationMembership.Role.VIEWER)

        return list(member_query.values_list("user__email", flat=True))


# Insert and delete rather than the default insert and update: adding and removing a country or
# sector are a row insert and a row delete, and a removal would otherwise go unrecorded.
@pghistory.track(pghistory.InsertEvent(), pghistory.DeleteEvent())
class OrganizationCountry(models.Model):
    """Explicit through model for Organization.countries.

    Declared explicitly only so its rows can be audited. Django's autodetector ignores
    auto-created through models, so pghistory generates an event model for one but never
    installs the triggers that would populate it.
    """

    organization = models.ForeignKey(Organization, on_delete=models.CASCADE)
    country = models.ForeignKey("opportunity.Country", on_delete=models.CASCADE)

    class Meta:
        # The table Django generated for the ManyToManyField, so this model adopts the existing
        # table and its rows instead of creating a new one.
        db_table = "organization_organization_countries"
        unique_together = [("organization", "country")]


@pghistory.track(pghistory.InsertEvent(), pghistory.DeleteEvent())
class OrganizationPrimarySector(models.Model):
    """Explicit through model for Organization.primary_sectors, declared so its rows can be audited."""

    organization = models.ForeignKey(Organization, on_delete=models.CASCADE)
    primary_sector = models.ForeignKey(PrimarySector, on_delete=models.CASCADE, db_column="primarysector_id")

    class Meta:
        db_table = "organization_organization_primary_sectors"
        unique_together = [("organization", "primary_sector")]


@pghistory.track(
    pghistory.InsertEvent(), pghistory.UpdateEvent(), pghistory.DeleteEvent(), exclude=UNTRACKED_MODEL_FIELDS
)
class Contact(BaseModel):
    """A named person at an organization.

    A contact lives only as long as its organization: deleting/archiving the organization deletes/archives
    the contact as well.
    """

    contact_id = models.UUIDField(editable=False, default=uuid4, unique=True)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="contacts")
    name = models.CharField(max_length=255)
    title = models.CharField(max_length=255, blank=True)
    email = models.EmailField()
    phone = models.CharField(max_length=32, blank=True)
    is_main_poc = models.BooleanField(default=False)
    notes = models.TextField(blank=True)
    is_archived = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization"],
                condition=models.Q(is_main_poc=True),
                name="one_main_poc_per_organization",
            ),
            # One contact per person, so a person is linked to only one organization.
            models.UniqueConstraint(
                Lower("email"),
                name="unique_contact_email",
                violation_error_message=_("A contact with this email already exists."),
            ),
        ]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        """Saves the contact, first unmarking the organization's other main contact if this one is now main."""
        with transaction.atomic():
            if self.is_main_poc:
                Contact.objects.filter(organization_id=self.organization_id, is_main_poc=True).exclude(
                    pk=self.pk
                ).update(is_main_poc=False)
            super().save(*args, **kwargs)


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
    accepted_at = models.DateTimeField(null=True, blank=True)

    @property
    def is_admin(self):
        return self.role == self.Role.ADMIN

    @property
    def is_member(self):
        return self.role == self.Role.MEMBER

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
        from commcare_connect.organization.tasks import send_invite_accepted_notification

        membership, _created = UserOrganizationMembership.objects.update_or_create(
            organization=self.organization, user=user, defaults={"role": self.role, "accepted_at": timezone.now()}
        )
        self.status = self.Status.ACCEPTED
        self.modified_by = user.email
        self.save(update_fields=["status", "modified_by", "date_modified"])
        transaction.on_commit(partial(send_invite_accepted_notification, membership.pk), robust=True)
        return membership
