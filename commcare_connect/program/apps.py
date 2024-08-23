from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class ProgramConfig(AppConfig):
    name = "commcare_connect.program"
    verbose_name = _("Program")
