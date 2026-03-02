from django.db import migrations
from django_celery_beat.models import CrontabSchedule, PeriodicTask


def create_auto_deactivate_periodic_task(apps, schema_editor):
    schedule, _ = CrontabSchedule.objects.get_or_create(
        minute="0",
        hour="0",
        day_of_week="*",
        day_of_month="*",
        month_of_year="*",
    )
    PeriodicTask.objects.update_or_create(
        name="auto_deactivate_ended_opportunities",
        defaults={
            "crontab": schedule,
            "task": "commcare_connect.opportunity.tasks.auto_deactivate_ended_opportunities",
        },
    )


def delete_auto_deactivate_periodic_task(apps, schema_editor):
    PeriodicTask.objects.filter(name="auto_deactivate_ended_opportunities").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("opportunity", "0115_opportunityactiveevent_opportunity_insert_insert_and_more"),
    ]

    operations = [
        migrations.RunPython(
            create_auto_deactivate_periodic_task,
            delete_auto_deactivate_periodic_task,
            hints={"run_on_secondary": False},
        )
    ]
