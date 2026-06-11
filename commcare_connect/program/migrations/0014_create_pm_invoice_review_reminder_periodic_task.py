from django.db import migrations
from django_celery_beat.models import CrontabSchedule, PeriodicTask


def create_pm_invoice_review_reminder_periodic_task(apps, schema_editor):
    schedule, _ = CrontabSchedule.objects.get_or_create(
        minute="0",
        hour="9",
        day_of_week="1",  # Monday
        day_of_month="*",
        month_of_year="*",
    )
    PeriodicTask.objects.update_or_create(
        name="send_pm_invoice_review_reminder",
        defaults={
            "crontab": schedule,
            "task": "commcare_connect.program.tasks.send_pm_invoice_review_reminder",
        },
    )


def delete_pm_invoice_review_reminder_periodic_task(apps, schema_editor):
    PeriodicTask.objects.filter(
        name="send_pm_invoice_review_reminder",
        task="commcare_connect.program.tasks.send_pm_invoice_review_reminder",
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("program", "0013_create_opportunity_expiry_reminder_periodic_task"),
    ]

    operations = [
        migrations.RunPython(
            create_pm_invoice_review_reminder_periodic_task,
            delete_pm_invoice_review_reminder_periodic_task,
            hints={"run_on_secondary": False},
        )
    ]
