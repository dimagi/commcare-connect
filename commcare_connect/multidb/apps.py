from django.apps import AppConfig
from django.conf import settings
from django.db.migrations import RunPython
from django.db.models.signals import pre_migrate
from django.dispatch import receiver


@receiver(pre_migrate)
def validate_migration_operations(sender, app_config, using, plan, **kwargs):
    """
    Validate that all RunPython operations are passing hints kwarg
    {'run_on_secondary': True/False}. For local apps only.
    """
    if app_config.name not in settings.LOCAL_APPS:
        return True

    for migration, backwards in plan:
        if migration.app_label != app_config.label:
            continue
        for operation in migration.operations:
            if isinstance(operation, RunPython):
                if not operation.hints:
                    raise Exception("RunPython must have 'hints={'run_on_secondary': True/False}'")
    return True


class MultidbConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "commcare_connect.multidb"
