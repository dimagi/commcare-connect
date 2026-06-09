import time
from collections import defaultdict

from django.core.management.base import BaseCommand, CommandError

from commcare_connect.opportunity.models import BlobMeta, UserVisit
from commcare_connect.opportunity.tasks import download_user_visit_attachments


class Command(BaseCommand):
    help = "Download attachments for user visits that reference blobs in form_json but are missing from BlobMeta."

    def add_arguments(self, parser):
        parser.add_argument(
            "--chunk-size",
            type=int,
            default=500,
            help="Number of user visits to inspect per database query. Defaults to 500.",
        )
        parser.add_argument(
            "--opportunity-id",
            type=int,
            help="Optional opportunity id filter to scope the scan.",
        )
        parser.add_argument(
            "--max-retries",
            type=int,
            default=3,
            help="Number of times to retry a failed download before giving up. Defaults to 3.",
        )
        parser.add_argument(
            "--delay",
            type=float,
            default=0.5,
            help="Seconds to sleep between downloads to avoid flooding HQ. Defaults to 0.5s.",
        )
        parser.add_argument(
            "--yes",
            action="store_true",
            help="Skip the interactive confirmation prompt.",
        )

    def handle(self, *args, **options):
        chunk_size = options["chunk_size"]
        opportunity_id = options.get("opportunity_id")
        max_retries = options["max_retries"]
        delay = options["delay"]
        auto_confirm = options["yes"]

        if chunk_size <= 0 or max_retries <= 0 or delay < 0:
            raise CommandError("chunk-size, max-retries must be positive and delay must be non-negative.")

        queryset = (
            UserVisit.objects.filter(form_json__has_key="attachments")
            .exclude(form_json__attachments={})
            .exclude(form_json__attachments__isnull=True)
            .order_by("id")
        )
        if opportunity_id:
            queryset = queryset.filter(opportunity_id=opportunity_id)

        total = queryset.count()
        if total == 0:
            self.stdout.write("No user visits with attachments matched the provided filters.")
            return

        self.stdout.write(f"Scanning {total} user visits (chunk_size={chunk_size}).")
        missing_visit_ids = []
        processed = 0
        visit_iterator = queryset.values("id", "xform_id", "form_json").iterator(chunk_size=chunk_size)
        buffer = []

        for visit in visit_iterator:
            buffer.append(visit)
            if len(buffer) == chunk_size:
                missing_visit_ids.extend(self._get_missing_user_visit_ids(buffer))
                processed += len(buffer)
                self._print_scan_progress(processed, total, len(missing_visit_ids))
                buffer = []

        if buffer:
            missing_visit_ids.extend(self._get_missing_user_visit_ids(buffer))
            processed += len(buffer)

        self._print_scan_progress(processed, total, len(missing_visit_ids), final=True)

        missing_count = len(missing_visit_ids)
        if missing_count == 0:
            self.stdout.write(self.style.SUCCESS("All user visits already have their attachments."))
            return

        self.stdout.write(self.style.WARNING(f"Found {missing_count} user visits with missing attachments."))
        if not auto_confirm and not self._confirm_proceed():
            self.stdout.write("Aborted without downloading any attachments.")
            return

        self._download_missing_visits(missing_visit_ids, chunk_size, max_retries, delay)

    def _confirm_proceed(self) -> bool:
        response = input("Download missing attachments now? [y/N]: ").strip().lower()
        return response in {"y", "yes"}

    def _print_scan_progress(self, processed, total, missing, final=False):
        percent = (processed / total) * 100 if total else 0
        stage = "Final" if final else "Progress"
        self.stdout.write(f"{stage}: processed {processed}/{total} visits ({percent:.1f}%), missing={missing}")

    def _download_missing_visits(self, visit_ids: list[int], chunk_size: int, max_retries: int, delay: float):
        total = len(visit_ids)
        failures = []
        processed = 0

        for start in range(0, total, chunk_size):
            batch_ids = visit_ids[start : start + chunk_size]  # noqa: E203
            for visit_id in batch_ids:
                processed += 1
                error = self._download_visit_with_retries(visit_id, processed, total, max_retries, delay)
                if error:
                    failures.append((visit_id, error))

        if failures:
            self.stderr.write(self.style.ERROR(f"{len(failures)} visits failed. Review and rerun as needed."))
        else:
            self.stdout.write(self.style.SUCCESS("All missing attachments downloaded successfully."))

    def _download_visit_with_retries(self, visit_id: int, index: int, total: int, max_retries: int, delay: float):
        for attempt in range(1, max_retries + 1):
            try:
                download_user_visit_attachments.run(visit_id)
            except Exception as exc:  # noqa: BLE001
                if attempt >= max_retries:
                    self.stderr.write(
                        self.style.ERROR(
                            f"[{index}/{total}] Failed to download attachments for visit {visit_id}: {exc}"
                        )
                    )
                    time.sleep(delay)
                    return str(exc)
                self.stderr.write(
                    f"[{index}/{total}] Error downloading visit {visit_id} "
                    f"(attempt {attempt}/{max_retries}). Retrying..."
                )
                time.sleep(delay)
            else:
                self.stdout.write(f"[{index}/{total}] Downloaded attachments for visit {visit_id}.")
                time.sleep(delay)
                return None

    def _get_missing_user_visit_ids(self, visits: list[dict]) -> list[int]:
        if not visits:
            return []

        parent_ids = [visit["xform_id"] for visit in visits]
        existing_blobs = (
            BlobMeta.objects.filter(parent_id__in=parent_ids).values_list("parent_id", "name") if parent_ids else []
        )

        blob_names_by_parent = defaultdict(set)
        for parent_id, name in existing_blobs:
            blob_names_by_parent[parent_id].add(name)

        missing_visit_ids = []
        for visit in visits:
            form_json = visit.get("form_json") or {}
            attachments = form_json.get("attachments")
            if not isinstance(attachments, dict):
                continue

            attachment_names = {name for name in attachments.keys() if name and name != "form.xml"}
            if not attachment_names:
                continue

            existing_names = blob_names_by_parent.get(visit["xform_id"], set())
            if attachment_names - existing_names:
                missing_visit_ids.append(visit["id"])

        return missing_visit_ids
