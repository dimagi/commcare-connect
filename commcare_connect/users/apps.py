from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class UsersConfig(AppConfig):
    name = "commcare_connect.users"
    verbose_name = _("Users")

    def ready(self):
        try:
            import commcare_connect.users.signals  # noqa: F401
        except ImportError:
            pass
