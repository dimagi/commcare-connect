from django.db import migrations

TASK_NAME = "send_delivery_stats_export"
FLAG_NAME = "delivery_stats_monthly_export"


def create_periodic_task_and_flag(apps, schema_editor):
    CrontabSchedule = apps.get_model("django_celery_beat", "CrontabSchedule")
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    Flag = apps.get_model("flags", "Flag")

    schedule, _ = CrontabSchedule.objects.get_or_create(
        minute="0",
        hour="2",
        day_of_week="*",
        day_of_month="1",
        month_of_year="*",
    )
    PeriodicTask.objects.get_or_create(
        name=TASK_NAME,
        defaults={
            "task": "commcare_connect.reports.tasks.send_delivery_stats_export",
            "crontab": schedule,
            "enabled": True,
        },
    )
    Flag.objects.get_or_create(
        name=FLAG_NAME,
        defaults={
            "everyone": None,
            "note": "Recipients of the monthly Delivery Stats CSV email. Long-lived; not a release toggle.",
        },
    )


def remove_periodic_task(apps, schema_editor):
    # The flag is kept so its recipient list survives a rollback.
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    PeriodicTask.objects.filter(name=TASK_NAME).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("reports", "0004_auto_20251205_0939"),
        ("flags", "0004_organization_label_wording"),
        ("django_celery_beat", "0018_improve_crontab_helptext"),
    ]

    operations = [
        migrations.RunPython(
            create_periodic_task_and_flag,
            remove_periodic_task,
            hints={"run_on_secondary": False},
        ),
    ]
