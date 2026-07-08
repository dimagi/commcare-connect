import secrets
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.urls import reverse
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


def _generate_invite_token():
    return secrets.token_urlsafe(32)


class OrganizationInvite(BaseModel):
    EXPIRY_DAYS = 7

    class Status(models.TextChoices):
        INVITED = "invited", _("Invited")
        ACCEPTED = "accepted", _("Accepted")
        REVOKED = "revoked", _("Revoked")
        EXPIRED = "expired", _("Expired")

    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="invites")
    email = models.EmailField()
    role = models.CharField(max_length=20, choices=UserOrganizationMembership.Role.choices)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.INVITED)
    token = models.CharField(max_length=64, unique=True, default=_generate_invite_token)
    invited_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name="sent_invites")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "email"],
                condition=models.Q(status="invited"),
                name="unique_pending_org_invite",
            )
        ]

    def __str__(self):
        return f"Invite for {self.email} to {self.organization}"

    def get_revoke_url(self):
        return reverse("organization:revoke_invite", args=(self.organization.slug, self.pk))

    @property
    def is_expired(self):
        return self.status == self.Status.INVITED and self.date_created < timezone.now() - timedelta(
            days=self.EXPIRY_DAYS
        )

    @classmethod
    def retire_expired(cls, organization, email):
        """Frees up the unique pending-invite slot so a lapsed invite can be re-sent."""
        cutoff = timezone.now() - timedelta(days=cls.EXPIRY_DAYS)
        cls.objects.filter(
            organization=organization, email=email, status=cls.Status.INVITED, date_created__lt=cutoff
        ).update(status=cls.Status.EXPIRED, modified_by=email)

    def accept(self, user):
        membership, _created = UserOrganizationMembership.objects.get_or_create(
            organization=self.organization, user=user, defaults={"role": self.role}
        )
        self.status = self.Status.ACCEPTED
        self.modified_by = user.email
        self.save(update_fields=["status", "modified_by", "date_modified"])
        return membership
