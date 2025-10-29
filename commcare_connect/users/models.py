from django.contrib.auth.models import AbstractUser
from django.contrib.auth.validators import UnicodeUsernameValidator
from django.db import models
from django.db.models import Q, UniqueConstraint
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from commcare_connect.commcarehq.models import HQServer
from commcare_connect.users.credential_levels import DeliveryLevel, LearnLevel
from commcare_connect.users.managers import UserManager


class User(AbstractUser):
    """
    Default custom user model for CommCare Connect.
    If adding fields that need to be filled at user signup,
    check forms.SignupForm and forms.SocialSignupForms accordingly.
    """

    username_validator = UnicodeUsernameValidator()

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

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    opportunity = models.ForeignKey("opportunity.Opportunity", on_delete=models.CASCADE)
    delivery_type = models.ForeignKey("opportunity.DeliveryType", on_delete=models.CASCADE)
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
        unique_together = ("user", "opportunity", "credential_type")

    @property
    def delivery_level_num(self):
        if self.credential_type == self.CredentialType.LEARN:
            return None
        return int(self.level.split("_")[0])

    @property
    def title(self):
        if self.credential_type == self.CredentialType.LEARN:
            return _("Passed learning assessment for {delivery_type}").format(delivery_type=self.delivery_type.name)
        return _("Completed {delivery_level_num} deliveries for {delivery_type}").format(
            delivery_level_num=self.delivery_level_num, delivery_type=self.delivery_type.name
        )
