# Generated by Django 4.2.5 on 2025-01-17 18:18

from django.db import migrations
from django_celery_beat.models import CrontabSchedule, PeriodicTask


def create_cache_report_task(apps, schema_editor):
    schedule, _ = CrontabSchedule.objects.get_or_create(
        minute="00",
        hour="01",
        day_of_week="*",
        day_of_month="*",
        month_of_year="*",
    )
    PeriodicTask.objects.update_or_create(
        crontab=schedule,
        name="prime_report_cache",
        task="commcare_connect.reports.tasks.prime_report_cache",
    )


def delete_cache_report_task(apps, schema_editor):
    PeriodicTask.objects.get(
        name="prime_report_cache",
        task="commcare_connect.reports.tasks.prime_report_cache",
    ).delete()


class Migration(migrations.Migration):
    dependencies = []

    operations = [
        migrations.RunPython(create_cache_report_task, delete_cache_report_task)
    ]