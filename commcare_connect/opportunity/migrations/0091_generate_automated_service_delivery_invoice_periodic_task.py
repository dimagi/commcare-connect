from django.db import migrations
from django_celery_beat.models import PeriodicTask, CrontabSchedule


def create_periodic_task(apps, schema_editor):
    # Run monthly on 5th at 1:00 AM
    schedule, _ = CrontabSchedule.objects.get_or_create(
        minute="00",
        hour="01",
        day_of_week="*",
        day_of_month="5",
        month_of_year="*",
    )
    PeriodicTask.objects.update_or_create(
        name="generate_automated_service_delivery_invoice",
        defaults={
            "task": "commcare_connect.opportunity.tasks.generate_automated_service_delivery_invoice",
            "crontab": schedule,
            "interval": None,
        },
    )


def delete_periodic_task(apps, schema_editor):
    PeriodicTask.objects.filter(
        name="generate_automated_service_delivery_invoice",
        task="commcare_connect.opportunity.tasks.generate_automated_service_delivery_invoice",
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("opportunity", "0090_backfill_invoice_status"),
    ]

    operations = [
        migrations.RunPython(
            create_periodic_task, delete_periodic_task,
            hints={"run_on_secondary": False}
        )
    ]
