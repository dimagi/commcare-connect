# Generated by Django 4.2.5 on 2024-04-22 06:10

from django.db import migrations
from django_celery_beat.models import CrontabSchedule, PeriodicTask


def create_bulk_apporve_completed_work_task(apps, schema_editor):
    schedule, _ = CrontabSchedule.objects.get_or_create(
        minute="00",
        hour="23",
        day_of_week="*",
        day_of_month="*",
        month_of_year="*",
    )
    PeriodicTask.objects.update_or_create(
        crontab=schedule,
        name="bulk_approve_completed_work",
        task="commcare_connect.opportunity.tasks.bulk_approve_completed_work",
    )


def delete_bulk_approve_completed_work_task(apps, schema_editor):
    PeriodicTask.objects.get(
        name="bulk_approve_completed_work",
        task="commcare_connect.opportunity.tasks.bulk_approve_completed_work",
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("opportunity", "0041_opportunity_auto_approve_payments_and_more"),
    ]

    operations = [
        migrations.RunPython(create_bulk_apporve_completed_work_task, delete_bulk_approve_completed_work_task)
    ]