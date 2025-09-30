from django.db import models
from django.utils.translation import gettext_lazy as _


class LearnLevel(models.TextChoices):
    LEARN_PASSED = "LEARN_PASSED", _("Learning passed")


class DeliveryLevel(models.TextChoices):
    # TODO: Confirm what values should be here
    pass
