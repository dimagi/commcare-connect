import secrets
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from commcare_connect.users.models import User
from commcare_connect.utils.db import BaseModel, slugify_uniquely


class LLOEntity(models.Model):
    name = models.CharField(max_length=255, unique=True)
    short_name = models.CharField(max_length=40, null=True, blank=True)

    class Meta:
        verbose_name_plural = "LLO Entities"

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


class OrgUserPaymentNumberStatus(models.Model):
    """
    This model stores whether a payment phone number of a
        Connect mobile user is working or not.
        The same is stored on ConnectID side per user level
        This model stores any overrides at org level.
    """

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"

    STATUS_CHOICES = [
        (PENDING, "Pending"),
        (APPROVED, "Approved"),
        (REJECTED, "Rejected"),
    ]

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
    )
    user = models.ForeignKey(
        User,
        on_delete=models.DO_NOTHING,
    )
    phone_number = models.CharField(max_length=15)
    status = models.CharField(
        max_length=10,
        choices=STATUS_CHOICES,
        default=PENDING,
    )

    class Meta:
        unique_together = ("user", "organization")


class OrganizationInvite(BaseModel):
    EXPIRY_DAYS = 7

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
    def is_expired(self):
        return self.status == self.Status.INVITED and self.date_modified < timezone.now() - timedelta(
            days=self.EXPIRY_DAYS
        )

    @classmethod
    def send_invite(cls, organization, email, role, invited_by):
        """Creates a pending invite, or resets an existing one (revoked/accepted/lapsed) to pending."""
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
