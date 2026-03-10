from django.db import migrations
from django_celery_beat.models import CrontabSchedule, PeriodicTask


def create_periodic_task(apps, schema_editor):
    schedule, _ = CrontabSchedule.objects.get_or_create(
        minute="0",
        hour="3",
        day_of_week="*",
        day_of_month="*",
        month_of_year="*",
    )
    PeriodicTask.objects.update_or_create(
        name="Cleanup expired export files",
        defaults={
            "task": "commcare_connect.opportunity.tasks.cleanup_expired_exports",
            "crontab": schedule,
            "interval": None,
        },
    )


def delete_periodic_task(apps, schema_editor):
    PeriodicTask.objects.filter(
        name="Cleanup expired export files",
        task="commcare_connect.opportunity.tasks.cleanup_expired_exports",
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("opportunity", "0116_exportfile"),
    ]

    operations = [
        migrations.RunPython(create_periodic_task, delete_periodic_task, hints={"run_on_secondary": False}),
    ]
