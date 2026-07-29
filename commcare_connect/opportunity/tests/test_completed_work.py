import pytest

from commcare_connect.opportunity.models import (
    CompletedWork,
    CompletedWorkStatus,
    VisitReviewStatus,
    VisitValidationStatus,
)
from commcare_connect.opportunity.tests.factories import (
    CompletedWorkFactory,
    DeliverUnitFactory,
    OpportunityAccessFactory,
    OpportunityFactory,
    PaymentUnitFactory,
    UserVisitFactory,
)
from commcare_connect.opportunity.utils.completed_work import update_status


@pytest.mark.django_db
class TestUpdateStatus:
    def _create_visit(self, completed_work, deliver_unit, **kwargs):
        opp_access = completed_work.opportunity_access
        if kwargs.get("status") == VisitValidationStatus.approved and "review_status" not in kwargs:
            kwargs["review_status"] = VisitReviewStatus.agree
        return UserVisitFactory(
            opportunity=opp_access.opportunity,
            user=opp_access.user,
            opportunity_access=opp_access,
            deliver_unit=deliver_unit,
            completed_work=completed_work,
            **kwargs,
        )

    def _run_update_status(self, completed_work):
        opp_access = completed_work.opportunity_access
        completed_works = CompletedWork.objects.filter(id=completed_work.id).select_related("payment_unit")
        update_status(completed_works, opp_access, compute_payment=True)
        completed_work.refresh_from_db()

    def test_completed_work_not_updated_to_approved_when_missing_required_visit(self):
        opp_access = OpportunityAccessFactory(opportunity__auto_approve_payments=True)
        payment_unit = PaymentUnitFactory(opportunity=opp_access.opportunity, amount=100)
        DeliverUnitFactory(
            app=opp_access.opportunity.deliver_app,
            payment_unit=payment_unit,
        )
        optional_deliver_unit = DeliverUnitFactory(
            app=opp_access.opportunity.deliver_app,
            payment_unit=payment_unit,
            optional=True,
        )

        completed_work = CompletedWorkFactory(
            status=CompletedWorkStatus.pending,
            opportunity_access=opp_access,
            payment_unit=payment_unit,
        )

        self._create_visit(completed_work, optional_deliver_unit, status=VisitValidationStatus.approved)
        self._run_update_status(completed_work)

        assert completed_work.status == CompletedWorkStatus.pending

    def test_completed_work_updated_to_approved_with_all_visits_approved(self):
        opp_access = OpportunityAccessFactory(opportunity__auto_approve_payments=True)
        payment_unit = PaymentUnitFactory(opportunity=opp_access.opportunity, amount=100)
        deliver_unit = DeliverUnitFactory(
            app=opp_access.opportunity.deliver_app,
            payment_unit=payment_unit,
        )

        completed_work = CompletedWorkFactory(
            status=CompletedWorkStatus.pending,
            opportunity_access=opp_access,
            payment_unit=payment_unit,
        )

        self._create_visit(completed_work, deliver_unit, status=VisitValidationStatus.approved)
        self._run_update_status(completed_work)

        assert completed_work.status == CompletedWorkStatus.approved
        assert completed_work.saved_approved_count == 1
        assert completed_work.saved_completed_count == 1
        assert completed_work.saved_payment_accrued == 100

    def test_completed_work_updated_to_approved_with_all_required_visits_approved(self):
        opp_access = OpportunityAccessFactory(opportunity__auto_approve_payments=True)
        payment_unit = PaymentUnitFactory(opportunity=opp_access.opportunity, amount=100)
        required_deliver_unit = DeliverUnitFactory(
            app=opp_access.opportunity.deliver_app,
            payment_unit=payment_unit,
        )
        optional_deliver_unit_1 = DeliverUnitFactory(
            app=opp_access.opportunity.deliver_app,
            payment_unit=payment_unit,
            optional=True,
        )
        optional_deliver_unit_2 = DeliverUnitFactory(
            app=opp_access.opportunity.deliver_app,
            payment_unit=payment_unit,
            optional=True,
        )

        completed_work = CompletedWorkFactory(
            status=CompletedWorkStatus.pending,
            opportunity_access=opp_access,
            payment_unit=payment_unit,
        )

        self._create_visit(completed_work, required_deliver_unit, status=VisitValidationStatus.approved)
        self._create_visit(completed_work, optional_deliver_unit_1, status=VisitValidationStatus.approved)
        self._create_visit(completed_work, optional_deliver_unit_2, status=VisitValidationStatus.pending)
        self._run_update_status(completed_work)

        assert completed_work.status == CompletedWorkStatus.approved
        assert completed_work.saved_approved_count == 1
        assert completed_work.saved_completed_count == 1
        assert completed_work.saved_payment_accrued == 100

    def test_completed_work_not_updated_to_approved_with_not_all_required_visits_approved(self):
        opp_access = OpportunityAccessFactory(opportunity__auto_approve_payments=True)
        payment_unit = PaymentUnitFactory(opportunity=opp_access.opportunity, amount=100)
        required_deliver_unit_1 = DeliverUnitFactory(
            app=opp_access.opportunity.deliver_app,
            payment_unit=payment_unit,
        )
        required_deliver_unit_2 = DeliverUnitFactory(
            app=opp_access.opportunity.deliver_app,
            payment_unit=payment_unit,
        )
        optional_deliver_unit = DeliverUnitFactory(
            app=opp_access.opportunity.deliver_app,
            payment_unit=payment_unit,
            optional=True,
        )

        completed_work = CompletedWorkFactory(
            status=CompletedWorkStatus.pending,
            opportunity_access=opp_access,
            payment_unit=payment_unit,
        )

        self._create_visit(completed_work, required_deliver_unit_1, status=VisitValidationStatus.approved)
        self._create_visit(completed_work, required_deliver_unit_2, status=VisitValidationStatus.pending)
        self._create_visit(completed_work, optional_deliver_unit, status=VisitValidationStatus.approved)
        self._run_update_status(completed_work)

        assert completed_work.status == CompletedWorkStatus.pending
        assert completed_work.saved_approved_count == 0
        assert completed_work.saved_completed_count == 1
        assert completed_work.saved_payment_accrued == 0

    def test_managed_opp_completed_work_not_updated_to_approved_without_agreement(self):
        opp_access = OpportunityAccessFactory(
            opportunity=OpportunityFactory(auto_approve_payments=True),
        )
        payment_unit = PaymentUnitFactory(opportunity=opp_access.opportunity, amount=100)
        required_deliver_unit = DeliverUnitFactory(
            app=opp_access.opportunity.deliver_app,
            payment_unit=payment_unit,
        )
        optional_deliver_unit = DeliverUnitFactory(
            app=opp_access.opportunity.deliver_app,
            payment_unit=payment_unit,
            optional=True,
        )

        completed_work = CompletedWorkFactory(
            status=CompletedWorkStatus.pending,
            opportunity_access=opp_access,
            payment_unit=payment_unit,
        )

        self._create_visit(
            completed_work,
            required_deliver_unit,
            status=VisitValidationStatus.approved,
            review_status=VisitReviewStatus.pending,
        )
        self._create_visit(
            completed_work,
            optional_deliver_unit,
            status=VisitValidationStatus.approved,
            review_status=VisitReviewStatus.agree,
        )
        self._run_update_status(completed_work)

        assert completed_work.status == CompletedWorkStatus.pending
        assert completed_work.saved_approved_count == 0
        assert completed_work.saved_completed_count == 1
        assert completed_work.saved_payment_accrued == 0

    def test_managed_opp_completed_work_updated_to_approved_with_agreement(self):
        opp_access = OpportunityAccessFactory(
            opportunity=OpportunityFactory(auto_approve_payments=True),
        )
        payment_unit = PaymentUnitFactory(opportunity=opp_access.opportunity, amount=100)
        required_deliver_unit = DeliverUnitFactory(
            app=opp_access.opportunity.deliver_app,
            payment_unit=payment_unit,
        )
        optional_deliver_unit = DeliverUnitFactory(
            app=opp_access.opportunity.deliver_app,
            payment_unit=payment_unit,
            optional=True,
        )

        completed_work = CompletedWorkFactory(
            status=CompletedWorkStatus.pending,
            opportunity_access=opp_access,
            payment_unit=payment_unit,
        )

        self._create_visit(
            completed_work,
            required_deliver_unit,
            status=VisitValidationStatus.approved,
            review_status=VisitReviewStatus.agree,
        )
        self._create_visit(
            completed_work,
            optional_deliver_unit,
            status=VisitValidationStatus.approved,
            review_status=VisitReviewStatus.agree,
        )
        self._run_update_status(completed_work)

        assert completed_work.status == CompletedWorkStatus.approved
        assert completed_work.saved_approved_count == 1
        assert completed_work.saved_completed_count == 1
        assert completed_work.saved_payment_accrued == 100

    def test_managed_opp_completed_work_updated_to_approved_with_same_unit_over_limit(self):
        opp_access = OpportunityAccessFactory(
            opportunity=OpportunityFactory(auto_approve_payments=True),
        )
        payment_unit = PaymentUnitFactory(opportunity=opp_access.opportunity, amount=100)
        required_deliver_unit = DeliverUnitFactory(
            app=opp_access.opportunity.deliver_app,
            payment_unit=payment_unit,
        )

        completed_work = CompletedWorkFactory(
            status=CompletedWorkStatus.pending,
            opportunity_access=opp_access,
            payment_unit=payment_unit,
        )

        self._create_visit(
            completed_work,
            required_deliver_unit,
            status=VisitValidationStatus.approved,
            review_status=VisitReviewStatus.agree,
        )
        self._create_visit(
            completed_work,
            required_deliver_unit,
            status=VisitValidationStatus.over_limit,
        )
        self._run_update_status(completed_work)

        assert completed_work.status == CompletedWorkStatus.approved
        assert completed_work.saved_approved_count == 1
        assert completed_work.saved_completed_count == 2
        assert completed_work.saved_payment_accrued == 100

    def test_managed_opp_completed_work_not_updated_to_approved_with_no_optional_visit(self):
        opp_access = OpportunityAccessFactory(
            opportunity=OpportunityFactory(auto_approve_payments=True),
        )
        payment_unit = PaymentUnitFactory(opportunity=opp_access.opportunity, amount=100)
        required_deliver_unit = DeliverUnitFactory(
            app=opp_access.opportunity.deliver_app,
            payment_unit=payment_unit,
        )
        DeliverUnitFactory(
            app=opp_access.opportunity.deliver_app,
            payment_unit=payment_unit,
            optional=True,
        )

        completed_work = CompletedWorkFactory(
            status=CompletedWorkStatus.pending,
            opportunity_access=opp_access,
            payment_unit=payment_unit,
        )

        self._create_visit(
            completed_work,
            required_deliver_unit,
            status=VisitValidationStatus.approved,
            review_status=VisitReviewStatus.agree,
        )
        self._run_update_status(completed_work)

        assert completed_work.status == CompletedWorkStatus.pending
        assert completed_work.saved_approved_count == 0
        assert completed_work.saved_completed_count == 0
        assert completed_work.saved_payment_accrued == 0

    def test_completed_work_updated_to_rejected_when_any_visit_rejected(self):
        opp_access = OpportunityAccessFactory(opportunity__auto_approve_payments=True)
        payment_unit = PaymentUnitFactory(opportunity=opp_access.opportunity, amount=100)
        deliver_unit = DeliverUnitFactory(
            app=opp_access.opportunity.deliver_app,
            payment_unit=payment_unit,
        )

        completed_work = CompletedWorkFactory(
            status=CompletedWorkStatus.pending,
            opportunity_access=opp_access,
            payment_unit=payment_unit,
        )

        self._create_visit(
            completed_work,
            deliver_unit,
            status=VisitValidationStatus.rejected,
            reason="Invalid data",
        )
        self._run_update_status(completed_work)

        assert completed_work.status == CompletedWorkStatus.rejected
        assert completed_work.reason == "Invalid data"
        assert completed_work.saved_approved_count == 0
        assert completed_work.saved_completed_count == 1
        assert completed_work.saved_payment_accrued == 0

    def test_payment_calculations_when_completed_work_approved(self):
        opp_access = OpportunityAccessFactory(opportunity__auto_approve_payments=True)
        payment_unit = PaymentUnitFactory(opportunity=opp_access.opportunity, amount=150)
        deliver_unit = DeliverUnitFactory(
            app=opp_access.opportunity.deliver_app,
            payment_unit=payment_unit,
        )

        completed_work = CompletedWorkFactory(
            status=CompletedWorkStatus.pending,
            opportunity_access=opp_access,
            payment_unit=payment_unit,
        )

        for _ in range(3):
            self._create_visit(completed_work, deliver_unit, status=VisitValidationStatus.approved)
        self._run_update_status(completed_work)

        assert completed_work.status == CompletedWorkStatus.approved
        assert completed_work.saved_approved_count == 3
        assert completed_work.saved_completed_count == 3
        assert completed_work.saved_payment_accrued == 450
        assert completed_work.saved_payment_accrued_usd > 0

    def test_no_status_update_when_auto_approve_disabled(self):
        opp_access = OpportunityAccessFactory(opportunity__auto_approve_payments=False)
        payment_unit = PaymentUnitFactory(opportunity=opp_access.opportunity, amount=100)
        deliver_unit = DeliverUnitFactory(
            app=opp_access.opportunity.deliver_app,
            payment_unit=payment_unit,
        )

        completed_work = CompletedWorkFactory(
            status=CompletedWorkStatus.pending,
            opportunity_access=opp_access,
            payment_unit=payment_unit,
        )

        self._create_visit(completed_work, deliver_unit, status=VisitValidationStatus.approved)
        self._run_update_status(completed_work)

        assert completed_work.status == CompletedWorkStatus.pending
        assert completed_work.saved_approved_count == 1
        assert completed_work.saved_completed_count == 1
        assert completed_work.saved_payment_accrued == 0

    def test_incomplete_completed_work_updated_to_pending_when_visits_not_yet_all_approved(self):
        opp_access = OpportunityAccessFactory(opportunity__auto_approve_payments=True)
        payment_unit = PaymentUnitFactory(opportunity=opp_access.opportunity, amount=100)
        deliver_unit = DeliverUnitFactory(
            app=opp_access.opportunity.deliver_app,
            payment_unit=payment_unit,
        )

        # CW starts at the model default: incomplete
        completed_work = CompletedWorkFactory(
            status=CompletedWorkStatus.incomplete,
            opportunity_access=opp_access,
            payment_unit=payment_unit,
        )

        self._create_visit(completed_work, deliver_unit, status=VisitValidationStatus.pending)
        self._run_update_status(completed_work)

        assert completed_work.status == CompletedWorkStatus.pending

    def test_rejected_completed_work_status_preserved_when_visits_not_all_approved(self):
        opp_access = OpportunityAccessFactory(opportunity__auto_approve_payments=True)
        payment_unit = PaymentUnitFactory(opportunity=opp_access.opportunity, amount=100)
        deliver_unit = DeliverUnitFactory(
            app=opp_access.opportunity.deliver_app,
            payment_unit=payment_unit,
        )

        completed_work = CompletedWorkFactory(
            status=CompletedWorkStatus.rejected,
            opportunity_access=opp_access,
            payment_unit=payment_unit,
        )

        self._create_visit(completed_work, deliver_unit, status=VisitValidationStatus.pending)
        self._run_update_status(completed_work)

        assert completed_work.status == CompletedWorkStatus.rejected

    def test_rejected_completed_work_updated_to_approved_when_all_visits_now_approved(self):
        opp_access = OpportunityAccessFactory(opportunity__auto_approve_payments=True)
        payment_unit = PaymentUnitFactory(opportunity=opp_access.opportunity, amount=100)
        deliver_unit = DeliverUnitFactory(
            app=opp_access.opportunity.deliver_app,
            payment_unit=payment_unit,
        )

        completed_work = CompletedWorkFactory(
            status=CompletedWorkStatus.rejected,
            opportunity_access=opp_access,
            payment_unit=payment_unit,
        )

        self._create_visit(completed_work, deliver_unit, status=VisitValidationStatus.approved)
        self._run_update_status(completed_work)

        assert completed_work.status == CompletedWorkStatus.approved

    def test_approved_completed_work_status_preserved_when_visit_reverted(self):
        opp_access = OpportunityAccessFactory(opportunity__auto_approve_payments=True)
        payment_unit = PaymentUnitFactory(opportunity=opp_access.opportunity, amount=100)
        deliver_unit = DeliverUnitFactory(
            app=opp_access.opportunity.deliver_app,
            payment_unit=payment_unit,
        )

        completed_work = CompletedWorkFactory(
            status=CompletedWorkStatus.approved,
            opportunity_access=opp_access,
            payment_unit=payment_unit,
        )

        self._create_visit(completed_work, deliver_unit, status=VisitValidationStatus.pending)
        self._run_update_status(completed_work)

        assert completed_work.status == CompletedWorkStatus.approved

    def test_managed_opp_saved_approved_count_uses_agreed_count(self):
        """For managed opps, saved_approved_count tallies only PM-agreed visits.

        An approved-but-unagreed duplicate must not raise the billable count.
        """
        opp_access = OpportunityAccessFactory(
            opportunity=OpportunityFactory(auto_approve_payments=True),
        )
        payment_unit = PaymentUnitFactory(opportunity=opp_access.opportunity, amount=100)
        deliver_unit = DeliverUnitFactory(
            app=opp_access.opportunity.deliver_app,
            payment_unit=payment_unit,
        )

        completed_work = CompletedWorkFactory(
            status=CompletedWorkStatus.approved,
            opportunity_access=opp_access,
            payment_unit=payment_unit,
        )

        # One agreed visit — the baseline bill
        self._create_visit(
            completed_work,
            deliver_unit,
            status=VisitValidationStatus.approved,
            review_status=VisitReviewStatus.agree,
        )
        # Approved but pending — must not raise the billable count
        self._create_visit(
            completed_work,
            deliver_unit,
            status=VisitValidationStatus.approved,
            review_status=VisitReviewStatus.pending,
        )
        # Approved but disagreed — must also not raise the billable count
        self._create_visit(
            completed_work,
            deliver_unit,
            status=VisitValidationStatus.approved,
            review_status=VisitReviewStatus.disagree,
        )
        self._run_update_status(completed_work)

        # Only the agreed visit should count toward the billable total
        assert completed_work.status == CompletedWorkStatus.approved
        assert completed_work.saved_approved_count == 1
        assert completed_work.saved_payment_accrued == 100
        assert completed_work.saved_completed_count == 3

    def test_managed_opp_billable_count_is_min_agreed_across_required_deliver_units(self):
        """Billable count is the minimum agreed count across required deliver units."""
        opp_access = OpportunityAccessFactory(
            opportunity=OpportunityFactory(auto_approve_payments=True),
        )
        payment_unit = PaymentUnitFactory(opportunity=opp_access.opportunity, amount=100)
        du1 = DeliverUnitFactory(app=opp_access.opportunity.deliver_app, payment_unit=payment_unit)
        du2 = DeliverUnitFactory(app=opp_access.opportunity.deliver_app, payment_unit=payment_unit)

        completed_work = CompletedWorkFactory(
            status=CompletedWorkStatus.approved,
            opportunity_access=opp_access,
            payment_unit=payment_unit,
        )

        # DU1: 3 agreed visits
        for _ in range(3):
            self._create_visit(
                completed_work,
                du1,
                status=VisitValidationStatus.approved,
                review_status=VisitReviewStatus.agree,
            )
        # DU2: 1 agreed + 2 approved-but-unagreed
        self._create_visit(
            completed_work,
            du2,
            status=VisitValidationStatus.approved,
            review_status=VisitReviewStatus.agree,
        )
        for _ in range(2):
            self._create_visit(
                completed_work,
                du2,
                status=VisitValidationStatus.approved,
                review_status=VisitReviewStatus.pending,
            )
        self._run_update_status(completed_work)

        # min(agreed_DU1=3, agreed_DU2=1) = 1
        assert completed_work.status == CompletedWorkStatus.approved
        assert completed_work.saved_approved_count == 1
        assert completed_work.saved_payment_accrued == 100
        assert completed_work.saved_completed_count == 3

    def test_managed_opp_billable_count_caps_at_agreed_optional_visits(self):
        """Optional unit's agreed count caps the billable total, not its approved count."""
        opp_access = OpportunityAccessFactory(
            opportunity=OpportunityFactory(auto_approve_payments=True),
        )
        payment_unit = PaymentUnitFactory(opportunity=opp_access.opportunity, amount=100)
        required_du = DeliverUnitFactory(app=opp_access.opportunity.deliver_app, payment_unit=payment_unit)
        optional_du = DeliverUnitFactory(
            app=opp_access.opportunity.deliver_app, payment_unit=payment_unit, optional=True
        )

        completed_work = CompletedWorkFactory(
            status=CompletedWorkStatus.approved,
            opportunity_access=opp_access,
            payment_unit=payment_unit,
        )

        for _ in range(3):
            self._create_visit(
                completed_work,
                required_du,
                status=VisitValidationStatus.approved,
                review_status=VisitReviewStatus.agree,
            )
        for _ in range(2):
            self._create_visit(
                completed_work,
                optional_du,
                status=VisitValidationStatus.approved,
                review_status=VisitReviewStatus.agree,
            )
        self._create_visit(
            completed_work,
            optional_du,
            status=VisitValidationStatus.approved,
            review_status=VisitReviewStatus.pending,
        )
        self._run_update_status(completed_work)

        # min(required_agreed=3, optional_agreed=2) = 2
        assert completed_work.status == CompletedWorkStatus.approved
        assert completed_work.saved_approved_count == 2
        assert completed_work.saved_payment_accrued == 200
        assert completed_work.saved_completed_count == 3

    def test_managed_opp_approved_completed_work_status_preserved_when_agreement_revoked(self):
        opp_access = OpportunityAccessFactory(
            opportunity=OpportunityFactory(auto_approve_payments=True),
        )
        payment_unit = PaymentUnitFactory(opportunity=opp_access.opportunity, amount=100)
        deliver_unit = DeliverUnitFactory(
            app=opp_access.opportunity.deliver_app,
            payment_unit=payment_unit,
        )

        completed_work = CompletedWorkFactory(
            status=CompletedWorkStatus.approved,
            opportunity_access=opp_access,
            payment_unit=payment_unit,
        )

        self._create_visit(
            completed_work,
            deliver_unit,
            status=VisitValidationStatus.approved,
            review_status=VisitReviewStatus.pending,
        )
        self._run_update_status(completed_work)

        assert completed_work.status == CompletedWorkStatus.approved
