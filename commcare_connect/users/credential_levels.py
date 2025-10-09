from django.db import models
from django.utils.translation import gettext_lazy as _


class LearnLevel(models.TextChoices):
    LEARN_PASSED = "LEARN_PASSED", _("Learning passed")


class DeliveryLevel(models.TextChoices):
    TWENTY_FIVE = "25_DELIVERIES", _("25 Deliveries")
    FIFTY = "50_DELIVERIES", _("50 Deliveries")
    ONE_HUNDRED = "100_DELIVERIES", _("100 Deliveries")
    TWO_HUNDRED = "200_DELIVERIES", _("200 Deliveries")
    FIVE_HUNDRED = "500_DELIVERIES", _("500 Deliveries")
    ONE_THOUSAND = "1000_DELIVERIES", _("1000 Deliveries")
