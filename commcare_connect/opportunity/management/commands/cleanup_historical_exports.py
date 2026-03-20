import re
from datetime import timedelta

try:
    import boto3
except ImportError:
    boto3 = None

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils.timezone import now

from commcare_connect.utils.itertools import batched

S3_BATCH_DELETE_LIMIT = 1000


EXPORT_FILENAME_PATTERNS = [
    re.compile(
        r"(exports/)?\d{4}-\d{2}-\d{2}T.*_("
        r"visit_export|review_visit_export|payment_export|"
        r"user_status|deliver_status|work_status|payment_verification|catchment_area"
        r")\.\w+"
    ),
    re.compile(r"(exports/)?invoice-report-[\w-]+\.csv"),
]


class Command(BaseCommand):
    help = "Clean up historical and current export files from S3."

    def add_arguments(self, parser):
        parser.add_argument(
            "--retention-days",
            type=int,
            default=30,
            help="Delete export files older than this many days. Defaults to 30.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="List files that would be deleted without actually deleting them.",
        )

    def handle(self, **options):
        if boto3 is None:
            raise CommandError("boto3 is required but not installed.")
        retention_days = options["retention_days"]
        if retention_days < 1:
            self.stderr.write(self.style.ERROR("--retention-days must be a positive integer."))
            return
        dry_run = options["dry_run"]
        cutoff = now() - timedelta(days=retention_days)

        bucket_name = settings.AWS_STORAGE_BUCKET_NAME
        s3 = boto3.resource("s3")
        bucket = s3.Bucket(bucket_name)

        to_delete = []
        scanned = 0

        for obj in bucket.objects.filter(Prefix="media/"):
            scanned += 1
            # Strip the media/ prefix for pattern matching
            filename = obj.key.removeprefix("media/")

            if not any(p.fullmatch(filename) for p in EXPORT_FILENAME_PATTERNS):
                continue

            if obj.last_modified >= cutoff:
                continue

            to_delete.append(obj.key)

        self.stdout.write(f"Scanned {scanned} objects under media/ prefix.")

        if not to_delete:
            self.stdout.write(self.style.SUCCESS("No expired export files found."))
            return

        if dry_run:
            self.stdout.write(self.style.WARNING(f"DRY RUN: Would delete {len(to_delete)} files:"))
            for key in to_delete:
                self.stdout.write(f"  {key}")
            return

        deleted_count = 0
        for batch in batched(to_delete, S3_BATCH_DELETE_LIMIT):
            response = bucket.delete_objects(Delete={"Objects": [{"Key": k} for k in batch]})
            errors = response.get("Errors", [])
            if errors:
                for err in errors:
                    self.stderr.write(f"Failed to delete {err['Key']}: {err['Code']} {err['Message']}")
            deleted_count += len(batch) - len(errors)

        self.stdout.write(self.style.SUCCESS(f"Deleted {deleted_count} expired export files."))
