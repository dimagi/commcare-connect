from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("opportunity", "0115_uservisit_work_area"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="task",
            name="time_estimate",
        ),
    ]
