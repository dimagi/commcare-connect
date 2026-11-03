import datetime
import wave

import pytest
from django.core.files.storage import default_storage
from django.core.management import call_command
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


class TestGenerateSampleData:
    """The command is a dev utility, so these cover the attachments it fabricates rather than
    every model it touches: that they exist, carry text, and are backed by real stored files."""

    @pytest.mark.parametrize("percent", [0, 100])
    def test_attachment_percent_decides_whether_attachments_appear(self, db, percent):
        call_command("generate_sample_data", 5, "attachment-sample-org", attachment_percent=percent)

        expected = percent > 0
        assert BlobMeta.objects.exists() is expected
        assert AudioAttachment.objects.exists() is expected

    def test_audio_carries_transcript_and_translation(self, db):
        call_command("generate_sample_data", 5, "attachment-sample-org", attachment_percent=100)

        for attachment in AudioAttachment.objects.all():
            assert attachment.transcript
            assert attachment.translation

    def test_stored_files_are_real_media(self, db):
        """The export and the UI both stream these back, so a placeholder would dead-end."""
        call_command("generate_sample_data", 5, "attachment-sample-org", attachment_percent=100)

        image = BlobMeta.objects.first()
        with default_storage.open(str(image.blob_id)) as stored:
            assert Image.open(stored).format == "JPEG"

        audio = AudioAttachment.objects.first()
        with default_storage.open(str(audio.blob_id)) as stored:
            assert wave.open(stored).getframerate() == SAMPLE_AUDIO_RATE

    def test_cleanup_removes_attachment_rows_and_their_files(self, db):
        """What a rerun relies on: BlobMeta is keyed by xform_id rather than a foreign key, so
        nothing cascades it, and the stored files would otherwise pile up in MEDIA_ROOT."""
        call_command("generate_sample_data", 5, "attachment-sample-org", attachment_percent=100)
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
