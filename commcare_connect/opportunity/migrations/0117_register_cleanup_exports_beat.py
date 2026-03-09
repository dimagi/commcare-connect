from django.db import migrations


def create_periodic_task(apps, schema_editor):
    IntervalSchedule = apps.get_model("django_celery_beat", "IntervalSchedule")
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")

    schedule, _ = IntervalSchedule.objects.get_or_create(
        every=24,
        period="hours",
    )
    PeriodicTask.objects.get_or_create(
        name="Cleanup expired export files",
        defaults={
            "task": "commcare_connect.opportunity.tasks.cleanup_expired_exports",
            "interval": schedule,
            "enabled": True,
        },
    )


def delete_periodic_task(apps, schema_editor):
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    PeriodicTask.objects.filter(name="Cleanup expired export files").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("opportunity", "0116_exportfile"),
        ("django_celery_beat", "0018_improve_crontab_helptext"),
    ]

    operations = [
        migrations.RunPython(create_periodic_task, delete_periodic_task, hints={"run_on_secondary": False}),
    ]
