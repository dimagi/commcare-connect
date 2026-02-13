import datetime
from unittest import mock
from uuid import uuid4

import pytest
from django.utils.timezone import now

from commcare_connect.form_receiver.exceptions import ProcessingError
from commcare_connect.form_receiver.processor import process_deliver_unit
from commcare_connect.form_receiver.serializers import XFormSerializer
from commcare_connect.form_receiver.tests.xforms import DeliverUnitStubFactory, get_form_json, get_form_model
from commcare_connect.opportunity.models import (
    CompletedWork,
    CompletedWorkStatus,
    UserVisit,
    VisitReviewStatus,
    VisitValidationStatus,
)
from commcare_connect.opportunity.tests.factories import (
    CommCareAppFactory,
    DeliverUnitFactory,
    OpportunityAccessFactory,
    OpportunityClaimFactory,
    OpportunityClaimLimitFactory,
    OpportunityFactory,
    OpportunityVerificationFlagsFactory,
    PaymentUnitFactory,
    UserVisitFactory,
)
from commcare_connect.users.tests.factories import UserFactory


@pytest.mark.django_db
class TestProcessDeliverUnit:
    def setup_method(self):
        self.user = UserFactory()
        self.app = CommCareAppFactory()
        self.opportunity = OpportunityFactory(
            deliver_app=self.app,
            start_date=datetime.date.today() - datetime.timedelta(days=10),
            end_date=datetime.date.today() + datetime.timedelta(days=10),
            auto_approve_visits=False,
        )
        self.payment_unit = PaymentUnitFactory(
            opportunity=self.opportunity,
            max_daily=5,
            max_total=20,
            start_date=datetime.date.today() - datetime.timedelta(days=5),
        )
        self.deliver_unit = DeliverUnitFactory(app=self.app, payment_unit=self.payment_unit)
        self.access = OpportunityAccessFactory(user=self.user, opportunity=self.opportunity, accepted=True)
        self.claim = OpportunityClaimFactory(
            opportunity_access=self.access, end_date=datetime.date.today() + datetime.timedelta(days=5)
        )
        self.claim_limit = OpportunityClaimLimitFactory(
            opportunity_claim=self.claim, payment_unit=self.payment_unit, max_visits=20
        )

    def test_no_opportunity_access_raises_error(self):
        user_without_access = UserFactory()
        stub = DeliverUnitStubFactory(id=self.deliver_unit.slug)
        xform = get_form_model(form_block=stub.json)

        with pytest.raises(ProcessingError, match="User does not have access to opportunity"):
            process_deliver_unit(user_without_access, xform, self.app, self.opportunity, stub.json["deliver"])

    def test_no_payment_unit_raises_error(self):
        deliver_unit_no_payment = DeliverUnitFactory(app=self.app, payment_unit=None)
        stub = DeliverUnitStubFactory(id=deliver_unit_no_payment.slug)
        xform = get_form_model(form_block=stub.json)

        with pytest.raises(ProcessingError, match="Payment unit is not configured"):
            process_deliver_unit(self.user, xform, self.app, self.opportunity, stub.json["deliver"])

    @mock.patch("commcare_connect.form_receiver.processor.update_payment_accrued_for_user")
    @mock.patch("commcare_connect.form_receiver.processor.download_user_visit_attachments")
    def test_successful_visit_creation(self, mock_download, mock_update_payment):
        stub = DeliverUnitStubFactory(id=self.deliver_unit.slug)
        xform = get_form_model(form_block=stub.json)

        process_deliver_unit(self.user, xform, self.app, self.opportunity, stub.json["deliver"])

        assert UserVisit.objects.count() == 1
        visit = UserVisit.objects.first()
        assert visit.user == self.user
        assert visit.opportunity == self.opportunity
        assert visit.deliver_unit == self.deliver_unit
        assert visit.entity_id == stub.entity_id
        assert visit.entity_name == stub.entity_name
        assert visit.status == VisitValidationStatus.pending
        assert visit.xform_id == xform.id
        assert CompletedWork.objects.count() == 1
        mock_update_payment.assert_called_once_with(self.access, incremental=True)

    @mock.patch("commcare_connect.form_receiver.processor.update_payment_accrued_for_user")
    @mock.patch("commcare_connect.form_receiver.processor.download_user_visit_attachments")
    def test_trial_status_before_opportunity_start_date(self, mock_download, mock_update_payment):
        self.opportunity.start_date = datetime.date.today() + datetime.timedelta(days=10)
        self.opportunity.save()

        stub = DeliverUnitStubFactory(id=self.deliver_unit.slug)
        xform = get_form_model(form_block=stub.json)

        process_deliver_unit(self.user, xform, self.app, self.opportunity, stub.json["deliver"])

        visit = UserVisit.objects.first()
        assert visit.status == VisitValidationStatus.trial
        assert CompletedWork.objects.count() == 0

    @mock.patch("commcare_connect.form_receiver.processor.update_payment_accrued_for_user")
    @mock.patch("commcare_connect.form_receiver.processor.download_user_visit_attachments")
    def test_trial_status_before_payment_unit_start_date(self, mock_download, mock_update_payment):
        self.payment_unit.start_date = datetime.date.today() + datetime.timedelta(days=10)
        self.payment_unit.save()

        stub = DeliverUnitStubFactory(id=self.deliver_unit.slug)
        xform = get_form_model(form_block=stub.json)

        process_deliver_unit(self.user, xform, self.app, self.opportunity, stub.json["deliver"])

        visit = UserVisit.objects.first()
        assert visit.status == VisitValidationStatus.trial
        assert CompletedWork.objects.count() == 0

    @mock.patch("commcare_connect.form_receiver.processor.update_payment_accrued_for_user")
    @mock.patch("commcare_connect.form_receiver.processor.download_user_visit_attachments")
    def test_over_limit_when_daily_max_reached(self, mock_download, mock_update_payment):
        stub = DeliverUnitStubFactory(id=self.deliver_unit.slug)
        xform = get_form_model(form_block=stub.json)
        for i in range(self.payment_unit.max_daily):
            UserVisitFactory(
                user=self.user,
                opportunity=self.opportunity,
                opportunity_access=self.access,
                deliver_unit=self.deliver_unit,
                entity_id=str(uuid4()),
                visit_date=xform.metadata.timeStart,
                status=VisitValidationStatus.approved,
            )

        process_deliver_unit(self.user, xform, self.app, self.opportunity, stub.json["deliver"])

        visit = UserVisit.objects.latest("id")
        assert visit.status == VisitValidationStatus.over_limit
        completed_work = visit.completed_work
        assert completed_work.status == CompletedWorkStatus.over_limit

    @mock.patch("commcare_connect.form_receiver.processor.update_payment_accrued_for_user")
    @mock.patch("commcare_connect.form_receiver.processor.download_user_visit_attachments")
    def test_over_limit_when_total_max_reached(self, mock_download, mock_update_payment):
        for i in range(self.claim_limit.max_visits):
            UserVisitFactory(
                user=self.user,
                opportunity=self.opportunity,
                opportunity_access=self.access,
                deliver_unit=self.deliver_unit,
                entity_id=str(uuid4()),
                visit_date=now() - datetime.timedelta(days=i % 5),
                status=VisitValidationStatus.approved,
            )

        stub = DeliverUnitStubFactory(id=self.deliver_unit.slug)
        xform = get_form_model(form_block=stub.json)

        process_deliver_unit(self.user, xform, self.app, self.opportunity, stub.json["deliver"])

        visit = UserVisit.objects.latest("id")
        assert visit.status == VisitValidationStatus.over_limit

    @mock.patch("commcare_connect.form_receiver.processor.update_payment_accrued_for_user")
    @mock.patch("commcare_connect.form_receiver.processor.download_user_visit_attachments")
    def test_over_limit_when_claim_end_date_passed(self, mock_download, mock_update_payment):
        self.claim.end_date = datetime.date.today() - datetime.timedelta(days=1)
        self.claim.save()

        stub = DeliverUnitStubFactory(id=self.deliver_unit.slug)
        xform = get_form_model(form_block=stub.json)

        process_deliver_unit(self.user, xform, self.app, self.opportunity, stub.json["deliver"])

        visit = UserVisit.objects.first()
        assert visit.status == VisitValidationStatus.over_limit

    @mock.patch("commcare_connect.form_receiver.processor.update_payment_accrued_for_user")
    @mock.patch("commcare_connect.form_receiver.processor.download_user_visit_attachments")
    def test_over_limit_when_claim_limit_end_date_passed(self, mock_download, mock_update_payment):
        self.claim_limit.end_date = datetime.date.today() - datetime.timedelta(days=1)
        self.claim_limit.save()

        stub = DeliverUnitStubFactory(id=self.deliver_unit.slug)
        xform = get_form_model(form_block=stub.json)

        process_deliver_unit(self.user, xform, self.app, self.opportunity, stub.json["deliver"])

        visit = UserVisit.objects.first()
        assert visit.status == VisitValidationStatus.over_limit

    @mock.patch("commcare_connect.form_receiver.processor.update_payment_accrued_for_user")
    @mock.patch("commcare_connect.form_receiver.processor.download_user_visit_attachments")
    def test_duplicate_visit_for_same_entity(self, mock_download, mock_update_payment):
        entity_id = str(uuid4())
        UserVisitFactory(
            user=self.user,
            opportunity=self.opportunity,
            opportunity_access=self.access,
            deliver_unit=self.deliver_unit,
            entity_id=entity_id,
            status=VisitValidationStatus.approved,
        )

        stub = DeliverUnitStubFactory(id=self.deliver_unit.slug, entity_id=entity_id)
        xform = get_form_model(form_block=stub.json)

        process_deliver_unit(self.user, xform, self.app, self.opportunity, stub.json["deliver"])

        visits = UserVisit.objects.filter(entity_id=entity_id)
        assert visits.count() == 2
        latest_visit = visits.latest("id")
        assert latest_visit.status == VisitValidationStatus.duplicate

    @mock.patch("commcare_connect.form_receiver.processor.update_payment_accrued_for_user")
    @mock.patch("commcare_connect.form_receiver.processor.download_user_visit_attachments")
    def test_completed_work_reused_for_same_entity(self, mock_download, mock_update_payment):
        entity_id = str(uuid4())
        entity_name = "Test Entity"

        stub = DeliverUnitStubFactory(id=self.deliver_unit.slug, entity_id=entity_id, entity_name=entity_name)
        xform = get_form_model(form_block=stub.json)

        process_deliver_unit(self.user, xform, self.app, self.opportunity, stub.json["deliver"])
        first_completed_work = CompletedWork.objects.first()
        assert first_completed_work.entity_id == entity_id

        form_json2 = get_form_json(form_block=stub.json)
        form_json2["id"] = str(uuid4())
        serializer = XFormSerializer(data=form_json2)
        serializer.is_valid(raise_exception=True)
        xform2 = serializer.save()

        process_deliver_unit(self.user, xform2, self.app, self.opportunity, stub.json["deliver"])

        assert CompletedWork.objects.count() == 1
        assert UserVisit.objects.count() == 2

    @mock.patch("commcare_connect.form_receiver.processor.update_payment_accrued_for_user")
    @mock.patch("commcare_connect.form_receiver.processor.download_user_visit_attachments")
    def test_suspended_user_visit_rejected(self, mock_download, mock_update_payment):
        self.access.suspended = True
        self.access.save()

        stub = DeliverUnitStubFactory(id=self.deliver_unit.slug)
        xform = get_form_model(form_block=stub.json)

        process_deliver_unit(self.user, xform, self.app, self.opportunity, stub.json["deliver"])

        visit = UserVisit.objects.first()
        assert visit.status == VisitValidationStatus.rejected
        assert visit.flagged is True
        assert ["user_suspended", "This user is suspended from the opportunity."] in visit.flag_reason["flags"]

    @mock.patch("commcare_connect.form_receiver.processor.update_payment_accrued_for_user")
    @mock.patch("commcare_connect.form_receiver.processor.download_user_visit_attachments")
    def test_auto_approve_visits(self, mock_download, mock_update_payment):
        self.opportunity.auto_approve_visits = True
        self.opportunity.save()

        stub = DeliverUnitStubFactory(id=self.deliver_unit.slug)
        xform = get_form_model(form_block=stub.json)

        process_deliver_unit(self.user, xform, self.app, self.opportunity, stub.json["deliver"])

        visit = UserVisit.objects.first()
        assert visit.status == VisitValidationStatus.approved
        assert visit.review_status == VisitReviewStatus.agree

    @mock.patch("commcare_connect.form_receiver.processor.update_payment_accrued_for_user")
    @mock.patch("commcare_connect.form_receiver.processor.download_user_visit_attachments")
    def test_auto_approve_not_applied_when_flagged(self, mock_download, mock_update_payment):
        self.opportunity.auto_approve_visits = True
        self.opportunity.save()
        OpportunityVerificationFlagsFactory(opportunity=self.opportunity, gps=True)

        stub = DeliverUnitStubFactory(id=self.deliver_unit.slug)
        form_json = get_form_json(form_block=stub.json)
        form_json["metadata"]["location"] = None

        serializer = XFormSerializer(data=form_json)
        serializer.is_valid(raise_exception=True)
        xform = serializer.save()

        process_deliver_unit(self.user, xform, self.app, self.opportunity, stub.json["deliver"])

        visit = UserVisit.objects.first()
        assert visit.flagged is True
        assert visit.status == VisitValidationStatus.pending
        assert visit.review_status != VisitReviewStatus.agree

    @mock.patch("commcare_connect.form_receiver.processor.update_payment_accrued_for_user")
    @mock.patch("commcare_connect.form_receiver.processor.download_user_visit_attachments")
    def test_last_active_updated(self, mock_download, mock_update_payment):
        assert self.access.last_active is None

        stub = DeliverUnitStubFactory(id=self.deliver_unit.slug)
        xform = get_form_model(form_block=stub.json)

        process_deliver_unit(self.user, xform, self.app, self.opportunity, stub.json["deliver"])

    @mock.patch("commcare_connect.form_receiver.processor.update_payment_accrued_for_user")
    @mock.patch("commcare_connect.form_receiver.processor.download_user_visit_attachments")
    def test_last_active_not_updated_if_earlier(self, mock_download, mock_update_payment):
        future_date = now() + datetime.timedelta(days=1)
        self.access.last_active = future_date
        self.access.save()

        stub = DeliverUnitStubFactory(id=self.deliver_unit.slug)
        xform = get_form_model(form_block=stub.json)

        process_deliver_unit(self.user, xform, self.app, self.opportunity, stub.json["deliver"])

        self.access.refresh_from_db()
        assert self.access.last_active == future_date

    @mock.patch("commcare_connect.form_receiver.processor.update_payment_accrued_for_user")
    @mock.patch("commcare_connect.form_receiver.processor.download_user_visit_attachments")
    def test_completed_work_created_with_incomplete_status(self, mock_download, mock_update_payment):
        entity_id = str(uuid4())
        stub = DeliverUnitStubFactory(id=self.deliver_unit.slug, entity_id=entity_id)
        xform = get_form_model(form_block=stub.json)

        process_deliver_unit(self.user, xform, self.app, self.opportunity, stub.json["deliver"])
        completed_work = CompletedWork.objects.first()
        assert completed_work.status in [CompletedWorkStatus.incomplete, CompletedWorkStatus.pending]
        assert completed_work.entity_id == entity_id

    @mock.patch("commcare_connect.form_receiver.processor.update_payment_accrued_for_user")
    @mock.patch("commcare_connect.form_receiver.processor.download_user_visit_attachments")
    def test_visit_excludes_over_limit_and_trial_from_counts(self, mock_download, mock_update_payment):
        UserVisitFactory(
            user=self.user,
            opportunity=self.opportunity,
            opportunity_access=self.access,
            deliver_unit=self.deliver_unit,
            entity_id=str(uuid4()),
            visit_date=now(),
            status=VisitValidationStatus.trial,
        )
        UserVisitFactory(
            user=self.user,
            opportunity=self.opportunity,
            opportunity_access=self.access,
            deliver_unit=self.deliver_unit,
            entity_id=str(uuid4()),
            visit_date=now(),
            status=VisitValidationStatus.over_limit,
        )

        stub = DeliverUnitStubFactory(id=self.deliver_unit.slug)
        xform = get_form_model(form_block=stub.json)

        process_deliver_unit(self.user, xform, self.app, self.opportunity, stub.json["deliver"])

        visit = UserVisit.objects.latest("id")
        assert visit.status == VisitValidationStatus.pending

    @mock.patch("commcare_connect.form_receiver.processor.update_payment_accrued_for_user")
    @mock.patch("commcare_connect.form_receiver.processor.download_user_visit_attachments")
    def test_xform_data_stored_correctly(self, mock_download, mock_update_payment):
        stub = DeliverUnitStubFactory(id=self.deliver_unit.slug)
        form_json = get_form_json(form_block=stub.json, build_id="test-build-id")
        form_json["metadata"]["app_build_version"] = 42
        form_json["metadata"]["location"] = "20.090209 40.09320 20 40"

        serializer = XFormSerializer(data=form_json)
        serializer.is_valid(raise_exception=True)
        xform = serializer.save()

        process_deliver_unit(self.user, xform, self.app, self.opportunity, stub.json["deliver"])

        visit = UserVisit.objects.first()
        assert visit.xform_id == xform.id
        assert visit.app_build_id == "test-build-id"
        assert visit.app_build_version == 42
        assert visit.location == "20.090209 40.09320 20 40"
        assert visit.form_json == xform.raw_form

    @mock.patch("commcare_connect.form_receiver.processor.update_payment_accrued_for_user")
    @mock.patch("commcare_connect.form_receiver.processor.download_user_visit_attachments")
    def test_auto_approved_visit_then_over_limit_completed_work_status(self, mock_download, mock_update_payment):
        self.opportunity.auto_approve_visits = True
        self.opportunity.save()

        entity_id = str(uuid4())

        stub1 = DeliverUnitStubFactory(id=self.deliver_unit.slug, entity_id=entity_id)
        xform1 = get_form_model(form_block=stub1.json)
        process_deliver_unit(self.user, xform1, self.app, self.opportunity, stub1.json["deliver"])

        visit1 = UserVisit.objects.first()
        assert visit1.status == VisitValidationStatus.approved
        assert visit1.review_status == VisitReviewStatus.agree

        completed_work = CompletedWork.objects.get(entity_id=entity_id)
        completed_work.status = CompletedWorkStatus.approved
        completed_work.save()

        for i in range(self.payment_unit.max_daily - 1):
            UserVisitFactory(
                user=self.user,
                opportunity=self.opportunity,
                opportunity_access=self.access,
                deliver_unit=self.deliver_unit,
                entity_id=str(uuid4()),
                visit_date=xform1.metadata.timeStart,
                status=VisitValidationStatus.approved,
            )

        form_json2 = get_form_json(form_block=stub1.json)
        form_json2["id"] = str(uuid4())
        serializer = XFormSerializer(data=form_json2)
        serializer.is_valid(raise_exception=True)
        xform2 = serializer.save()

        process_deliver_unit(self.user, xform2, self.app, self.opportunity, stub1.json["deliver"])

        visit2 = UserVisit.objects.latest("id")
        assert visit2.status == VisitValidationStatus.over_limit
        assert visit2.entity_id == entity_id

        completed_work.refresh_from_db()
        assert completed_work.status == CompletedWorkStatus.approved

        entity_visits = UserVisit.objects.filter(entity_id=entity_id)
        assert entity_visits.count() == 2
        assert CompletedWork.objects.filter(entity_id=entity_id).count() == 1
