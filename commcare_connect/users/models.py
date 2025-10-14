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
