from django.db import migrations


def create_periodic_task(apps, schema_editor):
    CrontabSchedule = apps.get_model("django_celery_beat", "CrontabSchedule")
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")

    schedule, _ = CrontabSchedule.objects.get_or_create(
        minute="0",
        hour="2",
        day_of_week="1",  # Monday
        day_of_month="*",
        month_of_year="*",
    )
    PeriodicTask.objects.get_or_create(
        name="audit.generate_audit_reports",
        defaults={
            "task": "audit.generate_audit_reports",
            "crontab": schedule,
            "enabled": True,
        },
    )


def remove_periodic_task(apps, schema_editor):
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    PeriodicTask.objects.filter(name="audit.generate_audit_reports").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("audit", "0001_initial"),
        ("django_celery_beat", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(
            create_periodic_task,
            remove_periodic_task,
            hints={"run_on_secondary": False},
        ),
    ]
