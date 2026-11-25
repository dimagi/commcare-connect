from django.db import migrations
from django_celery_beat.models import CrontabSchedule, PeriodicTask


def create_periodic_task(apps, schema_editor):
    schedule, _ = CrontabSchedule.objects.get_or_create(
        minute="0",
        hour="2",
        day_of_week="sun",
        day_of_month="*",
        month_of_year="*",
    )
    PeriodicTask.objects.update_or_create(
        name="clear_expired_oauth_tokens",
        defaults={
            "task": "commcare_connect.users.tasks.clear_expired_oauth_tokens",
            "crontab": schedule,
            "interval": None,
        },
    )


def delete_periodic_task(apps, schema_editor):
    PeriodicTask.objects.filter(
        name="clear_expired_oauth_tokens",
        task="commcare_connect.users.tasks.clear_expired_oauth_tokens",
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0027_remove_connectiduserlink_connect_user_and_more"),
        ("django_celery_beat", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(
            create_periodic_task,
            delete_periodic_task,
            hints={"run_on_secondary": False},
        )
    ]
