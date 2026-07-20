from django.db import migrations
from django_celery_beat.models import CrontabSchedule, PeriodicTask


def create_periodic_task(apps, schema_editor):
    schedule, _ = CrontabSchedule.objects.get_or_create(
        minute="0",
        hour="8",
        day_of_week="*",
        day_of_month="*",
        month_of_year="*",
    )
    PeriodicTask.objects.update_or_create(
        name="send_opportunity_expiry_reminders",
        defaults={
            "task": "commcare_connect.program.tasks.send_opportunity_expiry_reminders",
            "crontab": schedule,
            "interval": None,
        },
    )


def delete_periodic_task(apps, schema_editor):
    PeriodicTask.objects.filter(
        name="send_opportunity_expiry_reminders",
        task="commcare_connect.program.tasks.send_opportunity_expiry_reminders",
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("program", "0012_remove_managedopportunity_org_pay_per_visit"),
    ]

    operations = [
        migrations.RunPython(
            create_periodic_task,
            delete_periodic_task,
            hints={"run_on_secondary": False},
        )
    ]
