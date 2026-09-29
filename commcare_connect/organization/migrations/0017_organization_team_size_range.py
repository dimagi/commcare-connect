from django.db import migrations, models

from commcare_connect.utils.itertools import batched

BATCH_SIZE = 500

# Frozen copy of TeamSizeRange at the time of this migration: (value, floor, ceiling).
# A later edit to the choices must not rewrite what this migration did.
RANGES = (
    ("1-10", 1, 10),
    ("11-50", 11, 50),
    ("51-200", 51, 200),
    ("201-500", 201, 500),
    ("500+", 501, None),
)


def bucket(headcount):
    """The bracket a headcount falls into."""
    return next(value for value, _floor, ceiling in RANGES if ceiling is None or headcount <= ceiling)


def bucket_headcounts(apps, schema_editor):
    """Replaces each recorded headcount with the bracket it falls into."""
    Organization = apps.get_model("organization", "Organization")
    rows = Organization.objects.filter(team_size__isnull=False).iterator(chunk_size=BATCH_SIZE)
    for batch in batched(rows, BATCH_SIZE):
        for org in batch:
            org.team_size_range = bucket(org.team_size)
        Organization.objects.bulk_update(batch, ["team_size_range"])


def restore_headcounts(apps, schema_editor):
    """Reverses to the bracket's floor — the original headcount is not recoverable."""
    Organization = apps.get_model("organization", "Organization")
    floors = {value: floor for value, floor, _ceiling in RANGES}
    rows = Organization.objects.exclude(team_size_range="").iterator(chunk_size=BATCH_SIZE)
    for batch in batched(rows, BATCH_SIZE):
        for org in batch:
            org.team_size = floors[org.team_size_range]
        Organization.objects.bulk_update(batch, ["team_size"])


class Migration(migrations.Migration):
    dependencies = [
        ("organization", "0016_organization_is_test"),
    ]

    operations = [
        migrations.AddField(
            model_name="organization",
            name="team_size_range",
            field=models.CharField(
                blank=True,
                choices=[(value, value) for value, _floor, _ceiling in RANGES],
                max_length=20,
            ),
        ),
        # Organization is logically replicated, so the secondary picks up the bucketed
        # values without re-running the data migration there.
        migrations.RunPython(bucket_headcounts, restore_headcounts, hints={"run_on_secondary": False}),
        migrations.RemoveField(model_name="organization", name="team_size"),
        migrations.RenameField(model_name="organization", old_name="team_size_range", new_name="team_size"),
    ]
