from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class MicroplanningConfig(AppConfig):
    name = "commcare_connect.microplanning"
    verbose_name = _("Microplanning")
