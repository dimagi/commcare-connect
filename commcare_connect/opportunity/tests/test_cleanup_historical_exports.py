from datetime import datetime, timedelta, timezone
from io import StringIO
from unittest import mock

import pytest
from django.core.management import call_command

from commcare_connect.opportunity.management.commands.cleanup_historical_exports import EXPORT_FILENAME_PATTERNS


def _make_s3_object(key, last_modified):
    """Helper to build a mock S3 object summary."""
    obj = mock.MagicMock()
    obj.key = key
    obj.last_modified = last_modified
    return obj


class TestExportFilenamePatterns:
    @pytest.mark.parametrize(
        "filename",
        [
            # Historical (no exports/ prefix)
            "2026-03-09T10:00:00.000000_My_Opp_visit_export.csv",
            "2026-03-09T10:00:00.000000_My_Opp_review_visit_export.xlsx",
            "2026-03-09T10:00:00.000000_My_Opp_payment_export.csv",
            "2026-03-09T10:00:00.000000_My_Opp_user_status.csv",
            "2026-03-09T10:00:00.000000_My_Opp_deliver_status.csv",
            "2026-03-09T10:00:00.000000_My_Opp_work_status.csv",
            "2026-03-09T10:00:00.000000_My_Opp_payment_verification.csv",
            "2026-03-09T10:00:00.000000_My_Opp_catchment_area.csv",
            "invoice-report-550e8400-e29b-41d4-a716-446655440000.csv",
            # New (exports/ prefix)
            "exports/2026-03-09T10:00:00.000000_My_Opp_visit_export.csv",
            "exports/2026-03-09T10:00:00.000000_My_Opp_work_status.csv",
            "exports/invoice-report-550e8400-e29b-41d4-a716-446655440000.csv",
        ],
    )
    def test_matches_export_filenames(self, filename):
        assert any(p.fullmatch(filename) for p in EXPORT_FILENAME_PATTERNS)

    @pytest.mark.parametrize(
        "filename",
        [
            "550e8400-e29b-41d4-a716-446655440000",  # BlobMeta UUID
            "123_2026-03-09T10:00:00_visit_import",  # import file
            "work_area_upload-5-abc123.csv",  # work area import
            "random_file.txt",
        ],
    )
    def test_does_not_match_non_export_filenames(self, filename):
        assert not any(p.fullmatch(filename) for p in EXPORT_FILENAME_PATTERNS)


@pytest.mark.django_db
class TestCleanupHistoricalExportsCommand:
    @mock.patch("commcare_connect.opportunity.management.commands.cleanup_historical_exports.boto3")
    def test_dry_run_does_not_delete(self, mock_boto3, settings):
        settings.AWS_STORAGE_BUCKET_NAME = "test-bucket"
        old_date = datetime.now(timezone.utc) - timedelta(days=60)
        mock_bucket = mock.MagicMock()
        mock_boto3.resource.return_value.Bucket.return_value = mock_bucket
        mock_bucket.objects.filter.return_value = [
            _make_s3_object("media/2026-01-01T10:00:00_Opp_visit_export.csv", old_date),
        ]

        out = StringIO()
        call_command("cleanup_historical_exports", "--dry-run", "--retention-days=30", stdout=out)

        mock_bucket.delete_objects.assert_not_called()
        assert "DRY RUN" in out.getvalue()

    @mock.patch("commcare_connect.opportunity.management.commands.cleanup_historical_exports.boto3")
    def test_deletes_matching_old_files(self, mock_boto3, settings):
        settings.AWS_STORAGE_BUCKET_NAME = "test-bucket"
        old_date = datetime.now(timezone.utc) - timedelta(days=60)
        recent_date = datetime.now(timezone.utc) - timedelta(days=5)
        mock_bucket = mock.MagicMock()
        mock_boto3.resource.return_value.Bucket.return_value = mock_bucket
        mock_bucket.objects.filter.return_value = [
            _make_s3_object("media/2026-01-01T10:00:00_Opp_visit_export.csv", old_date),
            _make_s3_object("media/2026-03-05T10:00:00_Opp_visit_export.csv", recent_date),
            _make_s3_object("media/550e8400-e29b-41d4-a716-446655440000", old_date),  # BlobMeta
        ]
        mock_bucket.delete_objects.return_value = {"Deleted": [], "Errors": []}

        out = StringIO()
        call_command("cleanup_historical_exports", "--retention-days=30", stdout=out)

        mock_bucket.delete_objects.assert_called_once()
        deleted_keys = mock_bucket.delete_objects.call_args[1]["Delete"]["Objects"]
        assert len(deleted_keys) == 1
        assert deleted_keys[0]["Key"] == "media/2026-01-01T10:00:00_Opp_visit_export.csv"
