from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class SolicitationConfig(AppConfig):
    name = "commcare_connect.solicitation"
    verbose_name = _("Solicitation")
