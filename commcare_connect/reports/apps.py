from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class ReportsConfig(AppConfig):
    name = "commcare_connect.reports"
    verbose_name = _("Reports")
