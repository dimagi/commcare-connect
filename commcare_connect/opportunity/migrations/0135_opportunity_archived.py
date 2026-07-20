from django.db import migrations, models
from django_celery_beat.models import CrontabSchedule, PeriodicTask


def create_auto_archive_test_opps_periodic_task(apps, schema_editor):
    schedule, _ = CrontabSchedule.objects.get_or_create(
        minute="0",
        hour="0",
        day_of_week="*",
        day_of_month="*",
        month_of_year="*",
    )
    PeriodicTask.objects.update_or_create(
        name="auto_archive_test_opportunities",
        defaults={
            "crontab": schedule,
            "task": "commcare_connect.opportunity.tasks.auto_archive_test_opportunities",
        },
    )


def delete_auto_archive_test_opps_periodic_task(apps, schema_editor):
    PeriodicTask.objects.filter(name="auto_archive_test_opportunities").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("opportunity", "0134_add_program_fk_to_opportunity"),
    ]

    operations = [
        migrations.AddField(
            model_name="opportunity",
            name="archived",
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(
            create_auto_archive_test_opps_periodic_task,
            delete_auto_archive_test_opps_periodic_task,
            hints={"run_on_secondary": False},
        ),
    ]
