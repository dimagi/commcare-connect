from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class SimulationConfig(AppConfig):
    name = "commcare_connect.simulation"
    verbose_name = _("Simulation")
