from django.db import migrations

TASK_NAME = "send_delivery_stats_export"


def create_periodic_task(apps, schema_editor):
    CrontabSchedule = apps.get_model("django_celery_beat", "CrontabSchedule")
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")

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


def remove_periodic_task(apps, schema_editor):
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    PeriodicTask.objects.filter(name=TASK_NAME).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("reports", "0004_auto_20251205_0939"),
        ("django_celery_beat", "0018_improve_crontab_helptext"),
    ]

    operations = [
        migrations.RunPython(
            create_periodic_task,
            remove_periodic_task,
            hints={"run_on_secondary": False},
        ),
    ]
