from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class FlagsConfig(AppConfig):
    name = "commcare_connect.flags"
    verbose_name = _("Flags")
