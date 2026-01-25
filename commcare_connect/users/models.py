from uuid import uuid4

from django.contrib.auth.models import AbstractUser
from django.contrib.auth.validators import UnicodeUsernameValidator
from django.db import models
from django.db.models import Q, UniqueConstraint
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from commcare_connect.commcarehq.models import HQServer
from commcare_connect.users.managers import UserManager


class User(AbstractUser):
    """
    Default custom user model for CommCare Connect.
    If adding fields that need to be filled at user signup,
    check forms.SignupForm and forms.SocialSignupForms accordingly.
    """

    username_validator = UnicodeUsernameValidator()

    user_id = models.UUIDField(editable=False, null=True, default=uuid4)
    # First and last name do not cover name patterns around the globe
    name = models.CharField(_("Name of User"), blank=True, max_length=255)
    first_name = None  # type: ignore
    last_name = None  # type: ignore
    email = models.EmailField(_("email address"), null=True, blank=True)
    username = models.CharField(
        _("username"),
        max_length=150,
        unique=True,
        help_text=_("Required. 150 characters or fewer. Letters, digits and @/./+/-/_ only."),
        validators=[username_validator],
        error_messages={
            "unique": _("A user with that username already exists."),
        },
        null=True,
    )
    phone_number = models.CharField(max_length=15, null=True, blank=True)

    REQUIRED_FIELDS = []

    objects = UserManager()

    def get_absolute_url(self) -> str:
        """Get URL for user's detail view.

        Returns:
            str: URL for user detail.

        """
        return reverse("account_email")

    class Meta:
        constraints = [UniqueConstraint(fields=["email"], name="unique_user_email", condition=Q(email__isnull=False))]
        permissions = [
            ("demo_users_access", "Allow viewing OTPs for demo users"),
            ("otp_access", "Allow fetching OTPs for Connect users"),
            ("kpi_report_access", "Allow access to KPI reports"),
            ("all_org_access", "Allow admin access to all organizations"),
            ("view_commcarehq_form_link", "Can view CommCareHQ form link"),
            ("org_management_settings_access", "Can manage organizations settings"),
        ]

    def __str__(self):
        return self.email or self.username


class ConnectIDUserLink(models.Model):
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
    )
    commcare_username = models.TextField()
    domain = models.CharField(max_length=255, null=True, blank=True)
    hq_server = models.ForeignKey(HQServer, on_delete=models.DO_NOTHING, null=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "commcare_username"], name="connect_user")]


class UserCredential(models.Model):
    class CredentialType(models.TextChoices):
        LEARN = "LEARN", _("Learn")
        DELIVERY = "DELIVERY", _("Deliver")

    class LearnLevel(models.TextChoices):
        LEARN_PASSED = "LEARN_PASSED", _("Learning passed")

    class DeliveryLevel(models.TextChoices):
        TWENTY_FIVE = "25_DELIVERIES", _("25 Deliveries")
        FIFTY = "50_DELIVERIES", _("50 Deliveries")
        ONE_HUNDRED = "100_DELIVERIES", _("100 Deliveries")
        TWO_HUNDRED = "200_DELIVERIES", _("200 Deliveries")
        FIVE_HUNDRED = "500_DELIVERIES", _("500 Deliveries")
        ONE_THOUSAND = "1000_DELIVERIES", _("1000 Deliveries")

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    opportunity = models.ForeignKey("opportunity.Opportunity", on_delete=models.CASCADE)
    delivery_type = models.ForeignKey("opportunity.DeliveryType", on_delete=models.CASCADE, null=True, blank=True)
    created_on = models.DateTimeField(auto_now_add=True)
    issued_on = models.DateTimeField(null=True, blank=True)
    credential_type = models.CharField(
        max_length=32,
        choices=CredentialType.choices,
    )
    level = models.CharField(
        max_length=32,
        choices=DeliveryLevel.choices + LearnLevel.choices,
    )

    class Meta:
        unique_together = ("user", "opportunity", "credential_type", "level")

    @classmethod
    def delivery_level_num(cls, level: str, credential_type: str) -> int | None:
        if credential_type == cls.CredentialType.LEARN:
            return None
        return int(level.split("_")[0])

    @classmethod
    def get_title(cls, credential_type: str, level: str, delivery_type_name: str) -> str:
        if credential_type == cls.CredentialType.LEARN:
            return _("Passed learning assessment for {earned_for}").format(earned_for=delivery_type_name)

        return _("Completed {delivery_level_num} deliveries for {earned_for}").format(
            delivery_level_num=cls.delivery_level_num(level, credential_type),
            earned_for=delivery_type_name,
        )
