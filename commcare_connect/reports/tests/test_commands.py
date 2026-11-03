import datetime
import io
import wave

import pytest
from django.core.files.storage import default_storage
from django.core.management import call_command
from django.core.management.base import CommandError
from PIL import Image

from commcare_connect.opportunity.models import (
    AudioAttachment,
    BlobMeta,
    CompletedWork,
    CompletedWorkStatus,
    OpportunityAccess,
)
from commcare_connect.opportunity.tests.factories import (
    AssessmentFactory,
    CompletedWorkFactory,
    OpportunityAccessFactory,
    OpportunityClaimFactory,
    OpportunityFactory,
    PaymentFactory,
    PaymentUnitFactory,
)
from commcare_connect.organization.models import Organization
from commcare_connect.reports.management.commands.generate_sample_data import SAMPLE_AUDIO_RATE, Command
from commcare_connect.reports.models import UserAnalyticsData
from commcare_connect.users.tests.factories import UserFactory


class TestBackfillUserAnalyticsData:
    def test_data_for_connect_user(self, db, httpx_mock):
        httpx_mock.add_response(method="GET", json={"data": [{"username": "test"}]})
        user = UserFactory(username="test", email=None)
        for idx, opportunity in enumerate(OpportunityFactory.create_batch(2)):
            payment_unit = PaymentUnitFactory(opportunity=opportunity, max_total=1)

            access = OpportunityAccessFactory(
                user=user,
                opportunity=opportunity,
                date_learn_started=datetime.datetime(2023 + idx, 1, 2, tzinfo=datetime.UTC),
                completed_learn_date=datetime.datetime(2023 + idx, 1, 3, tzinfo=datetime.UTC),
                accepted=True,
            )

            AssessmentFactory(
                opportunity_access=access,
                passed=True,
                date=datetime.datetime(2023 + idx, 1, 4, tzinfo=datetime.UTC),
            )

            claim = OpportunityClaimFactory(opportunity_access=access)
            claim.date_claimed = datetime.datetime(2023 + idx, 1, 5, tzinfo=datetime.UTC)
            claim.save()

            completed_work = CompletedWorkFactory(
                opportunity_access=access,
                payment_unit=payment_unit,
                status=CompletedWorkStatus.approved,
                status_modified_date=datetime.datetime(2023 + idx, 1, 9, tzinfo=datetime.UTC),
            )
            completed_work.date_created = datetime.datetime(2023 + idx, 1, 6, tzinfo=datetime.UTC)
            completed_work.save()

            PaymentFactory(
                opportunity_access=access,
                date_paid=datetime.datetime(2023 + idx, 1, 7, tzinfo=datetime.UTC),
            )

        assert not UserAnalyticsData.objects.filter(user=user).exists()
        call_command("backfill_user_analytics_data")
        assert UserAnalyticsData.objects.filter(user=user).exists()

        analytics_data = UserAnalyticsData.objects.get(user=user)
        access = OpportunityAccess.objects.filter(user=user).order_by("invited_date").first()
        assert analytics_data.username == user.username
        assert analytics_data.has_opp_invite == access.invited_date
        assert analytics_data.has_started_learning == access.date_learn_started
        assert analytics_data.has_completed_learning == access.completed_learn_date
        assert analytics_data.has_completed_assessment == access.assessment_set.all().order_by("date").first().date
        assert analytics_data.has_claimed_job.date() == access.opportunityclaim.date_claimed
        first_cw = CompletedWork.objects.filter(opportunity_access__user=user).order_by("date_created").first()
        assert analytics_data.has_ever_earned_payment == first_cw.status_modified_date
        assert analytics_data.has_started_job == first_cw.date_created
        assert analytics_data.has_been_paid == access.payment_set.all().order_by("date_paid").first().date_paid
        assert analytics_data.has_completed_opp == completed_work.status_modified_date
        assert analytics_data.has_offered_multiple_opps == access.invited_date
        assert analytics_data.has_accepted_multiple_opps == access.invited_date
        assert analytics_data.has_completed_multiple_opps == completed_work.status_modified_date

    def test_data_for_personalid_user(self, db, httpx_mock):
        httpx_mock.add_response(method="GET", json={"data": [{"username": "test"}]})

        assert not UserAnalyticsData.objects.filter(username="test", user__isnull=True).exists()
        call_command("backfill_user_analytics_data")
        assert UserAnalyticsData.objects.filter(username="test", user__isnull=True).exists()

        analytics_data = UserAnalyticsData.objects.get(username="test")
        assert analytics_data.username == "test"
        assert analytics_data.has_opp_invite is None
        assert analytics_data.has_ever_earned_payment is None
        assert analytics_data.has_started_learning is None
        assert analytics_data.has_completed_learning is None
        assert analytics_data.has_completed_assessment is None
        assert analytics_data.has_claimed_job is None
        assert analytics_data.has_started_job is None
        assert analytics_data.has_been_paid is None
        assert analytics_data.has_completed_opp is None
        assert analytics_data.has_offered_multiple_opps is None
        assert analytics_data.has_accepted_multiple_opps is None
        assert analytics_data.has_completed_multiple_opps is None


def _generate(*, image_percent=0, audio_percent=0, media_dir=None):
    """Runs the command with attachments off, so each test turns on only what it asserts."""
    call_command(
        "generate_sample_data",
        5,
        "attachment-sample-org",
        image_percent=image_percent,
        audio_percent=audio_percent,
        media_dir=media_dir,
    )


def _write_jpeg(path, color=(70, 130, 180)):
    path.parent.mkdir(parents=True, exist_ok=True)
    buffer = io.BytesIO()
    Image.new("RGB", (64, 48), color).save(buffer, format="JPEG")
    path.write_bytes(buffer.getvalue())
    return buffer.getvalue()


def _write_wav(path, seconds=1):
    """Longer than the generated placeholder by default, so the bytes are distinguishable."""
    path.parent.mkdir(parents=True, exist_ok=True)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as clip:
        clip.setnchannels(1)
        clip.setsampwidth(1)
        clip.setframerate(SAMPLE_AUDIO_RATE)
        clip.writeframes(b"\x80" * (SAMPLE_AUDIO_RATE * seconds))
    path.write_bytes(buffer.getvalue())
    return buffer.getvalue()


class TestGenerateSampleData:
    """The command is a dev utility, so these cover the attachments it fabricates rather than
    every model it touches: that they exist, carry text, and are backed by real stored files."""

    def test_no_attachments_by_default(self, db):
        """Both percentages default to off, so a plain run writes nothing to MEDIA_ROOT."""
        call_command("generate_sample_data", 5, "attachment-sample-org")

        assert not BlobMeta.objects.exists()
        assert not AudioAttachment.objects.exists()

    @pytest.mark.parametrize(
        ("image_percent", "audio_percent", "wants_images", "wants_audio"),
        [
            (0, 0, False, False),
            (100, 0, True, False),
            (0, 100, False, True),
            (100, 100, True, True),
        ],
    )
    def test_the_two_percentages_are_independent(self, db, image_percent, audio_percent, wants_images, wants_audio):
        _generate(image_percent=image_percent, audio_percent=audio_percent)

        assert BlobMeta.objects.exists() is wants_images
        assert AudioAttachment.objects.exists() is wants_audio

    def test_audio_carries_transcript_and_translation(self, db):
        _generate(audio_percent=100)

        for attachment in AudioAttachment.objects.all():
            assert attachment.transcript
            assert attachment.translation

    def test_stored_files_are_real_media(self, db):
        """The export and the UI both stream these back, so a placeholder would dead-end."""
        _generate(image_percent=100, audio_percent=100)

        image = BlobMeta.objects.first()
        with default_storage.open(str(image.blob_id)) as stored:
            assert Image.open(stored).format == "JPEG"

        audio = AudioAttachment.objects.first()
        with default_storage.open(str(audio.blob_id)) as stored:
            assert wave.open(stored).getframerate() == SAMPLE_AUDIO_RATE

    def test_media_dir_supplies_the_attachment_bytes(self, db, tmp_path):
        """A real directory is the point of the option: the stored file has to be the one on disk,
        not a placeholder, and its recorded length has to match."""
        image_bytes = _write_jpeg(tmp_path / "clinic.jpeg", color=(10, 20, 30))
        audio_bytes = _write_wav(tmp_path / "nested" / "reply.wav", seconds=2)

        _generate(image_percent=100, audio_percent=100, media_dir=str(tmp_path))

        blob = BlobMeta.objects.first()
        assert blob.name == "photo.jpeg"
        assert blob.content_type == "image/jpeg"
        assert blob.content_length == len(image_bytes)
        with default_storage.open(str(blob.blob_id)) as stored:
            assert stored.read() == image_bytes

        attachment = AudioAttachment.objects.first()
        assert attachment.name.endswith(".wav")
        assert attachment.content_length == len(audio_bytes)
        with default_storage.open(str(attachment.blob_id)) as stored:
            assert stored.read() == audio_bytes

    def test_media_dir_falls_back_per_kind(self, db, tmp_path):
        """A directory of audio alone still produces photos, from the placeholder."""
        audio_bytes = _write_wav(tmp_path / "only.wav", seconds=2)

        _generate(image_percent=100, audio_percent=100, media_dir=str(tmp_path))

        with default_storage.open(str(AudioAttachment.objects.first().blob_id)) as stored:
            assert stored.read() == audio_bytes
        image = BlobMeta.objects.first()
        assert image.name == "photo.jpg"
        with default_storage.open(str(image.blob_id)) as stored:
            assert Image.open(stored).format == "JPEG"

    def test_media_dir_ignores_files_that_are_neither(self, db, tmp_path):
        (tmp_path / "notes.txt").write_text("not media")
        _write_jpeg(tmp_path / "clinic.jpg")

        _generate(image_percent=100, audio_percent=0, media_dir=str(tmp_path))

        assert {blob.name for blob in BlobMeta.objects.all()} == {"photo.jpg"}

    def test_missing_media_dir_fails_loudly(self, db, tmp_path):
        with pytest.raises(CommandError, match="not a directory"):
            _generate(media_dir=str(tmp_path / "nope"))

    def test_cleanup_removes_attachment_rows_and_their_files(self, db):
        """What a rerun relies on: BlobMeta is keyed by xform_id rather than a foreign key, so
        nothing cascades it, and the stored files would otherwise pile up in MEDIA_ROOT."""
        _generate(image_percent=100, audio_percent=100)
        blob_ids = [
            *BlobMeta.objects.values_list("blob_id", flat=True),
            *AudioAttachment.objects.values_list("blob_id", flat=True),
        ]

        Command()._delete_attachments(list(Organization.objects.values_list("id", flat=True)))

        assert blob_ids
        assert not BlobMeta.objects.exists()
        assert not AudioAttachment.objects.exists()
        for blob_id in blob_ids:
            assert not default_storage.exists(str(blob_id))
