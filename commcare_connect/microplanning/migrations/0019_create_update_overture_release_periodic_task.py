from django.db import migrations
from django_celery_beat.models import CrontabSchedule, PeriodicTask


def create_update_overture_release_periodic_task(apps, schema_editor):
    schedule, _ = CrontabSchedule.objects.get_or_create(
        minute="0",
        hour="0",
        day_of_week="*",
        day_of_month="*",
        month_of_year="*",
    )
    PeriodicTask.objects.update_or_create(
        name="update_overture_release",
        defaults={
            "task": "commcare_connect.microplanning.tasks.update_overture_release",
            "crontab": schedule,
            "interval": None,
        },
    )


def delete_update_overture_release_periodic_task(apps, schema_editor):
    PeriodicTask.objects.filter(
        name="update_overture_release",
        task="commcare_connect.microplanning.tasks.update_overture_release",
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("microplanning", "0018_overturerelease"),
        ("django_celery_beat", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(
            create_update_overture_release_periodic_task,
            delete_update_overture_release_periodic_task,
            hints={"run_on_secondary": False},
        )
    ]
