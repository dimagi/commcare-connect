from django.db import migrations
from django_celery_beat.models import CrontabSchedule, PeriodicTask


def create_periodic_task(apps, schema_editor):
    schedule, _ = CrontabSchedule.objects.get_or_create(
        minute="0",
        hour="2",
        day_of_week="mon",
        day_of_month="*",
        month_of_year="*",
    )
    PeriodicTask.objects.update_or_create(
        name="delete_stale_opportunities",
        defaults={
            "task": "commcare_connect.opportunity.tasks.delete_stale_opportunities",
            "crontab": schedule,
            "interval": None,
        },
    )


def delete_periodic_task(apps, schema_editor):
    PeriodicTask.objects.filter(
        name="delete_stale_opportunities",
        task="commcare_connect.opportunity.tasks.delete_stale_opportunities",
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("opportunity", "0097_remove_opportunity_currency"),
    ]

    operations = [
        migrations.RunPython(
            create_periodic_task,
            delete_periodic_task,
            hints={"run_on_secondary": False},
        )
    ]
