from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Count

from commcare_connect.audit.models import Assessment


class Command(BaseCommand):
    help = "Clean up duplicate assessments in the database"

    def handle(self, *args, **options):
        self.stdout.write("Finding duplicate assessments...")

        # Find all duplicate groups
        duplicate_groups = list(
            Assessment.objects.values("audit_result", "assessment_type", "blob_id")
            .annotate(count=Count("id"))
            .filter(count__gt=1)
        )

        self.stdout.write(f"Found {len(duplicate_groups)} groups of duplicates")

        if not duplicate_groups:
            self.stdout.write(self.style.SUCCESS("No duplicates found!"))
            return

        deleted_count = 0

        with transaction.atomic():
            for i, dup_group in enumerate(duplicate_groups):
                # Get all assessments in this duplicate group
                assessments = Assessment.objects.filter(
                    audit_result_id=dup_group["audit_result"],
                    assessment_type=dup_group["assessment_type"],
                    blob_id=dup_group["blob_id"],
                ).order_by("id")

                # Keep the first one (with any existing result/notes), delete the rest
                first_assessment = assessments.first()
                duplicates_to_delete = assessments.exclude(id=first_assessment.id)

                count = duplicates_to_delete.count()
                if count > 0:
                    duplicates_to_delete.delete()
                    deleted_count += count

                if (i + 1) % 100 == 0:
                    self.stdout.write(f"  Processed {i + 1} groups, deleted {deleted_count} duplicates so far...")

        self.stdout.write(self.style.SUCCESS(f"\nTotal duplicates deleted: {deleted_count}"))
        self.stdout.write(self.style.SUCCESS("Cleanup complete!"))
