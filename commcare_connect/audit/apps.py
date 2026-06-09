from django.apps import AppConfig


class AuditConfig(AppConfig):
    name = "commcare_connect.audit"
    verbose_name = "Audit"

    def ready(self):
        # Ensure calculation subclasses are registered at Django startup,
        # regardless of which code path first touches the registry.
        from commcare_connect.audit import calculations  # noqa: F401
