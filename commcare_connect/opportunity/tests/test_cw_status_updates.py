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
    PaymentUnitFactory,
    UserVisitFactory,
)
from commcare_connect.opportunity.utils.completed_work import update_status
from commcare_connect.program.tests.factories import ManagedOpportunityFactory


def _run_update(completed_work):
    """Helper to run update_status and refresh the completed_work."""
    completed_works = CompletedWork.objects.filter(id=completed_work.id).select_related("payment_unit")
    update_status(completed_works, completed_work.opportunity_access, compute_payment=True)
    completed_work.refresh_from_db()


def _setup_non_managed(auto_approve=True):
    """Create a non-managed opportunity with auto_approve_payments."""
    opp_access = OpportunityAccessFactory()
    opp_access.opportunity.auto_approve_payments = auto_approve
    opp_access.opportunity.save()
    return opp_access


def _setup_managed(auto_approve=True):
    """Create a managed opportunity with auto_approve_payments."""
    managed_opp = ManagedOpportunityFactory()
    opp_access = OpportunityAccessFactory()
    opp_access.opportunity = managed_opp
    opp_access.opportunity.auto_approve_payments = auto_approve
    opp_access.opportunity.save()
    opp_access.save()
    return opp_access


def _make_payment_unit(opp_access, amount=100):
    return PaymentUnitFactory(opportunity=opp_access.opportunity, amount=amount)


def _make_required_du(opp_access, payment_unit):
    return DeliverUnitFactory(app=opp_access.opportunity.deliver_app, payment_unit=payment_unit)


def _make_optional_du(opp_access, payment_unit):
    return DeliverUnitFactory(app=opp_access.opportunity.deliver_app, payment_unit=payment_unit, optional=True)


def _make_cw(opp_access, payment_unit, status=CompletedWorkStatus.pending):
    return CompletedWorkFactory(status=status, opportunity_access=opp_access, payment_unit=payment_unit)


def _make_visit(opp_access, deliver_unit, completed_work, status, review_status=None, reason=""):
    kwargs = dict(
        opportunity=opp_access.opportunity,
        user=opp_access.user,
        opportunity_access=opp_access,
        deliver_unit=deliver_unit,
        completed_work=completed_work,
        status=status,
    )
    if review_status is not None:
        kwargs["review_status"] = review_status
    if reason:
        kwargs["reason"] = reason
    return UserVisitFactory(**kwargs)


# =============================================================================
# Non-managed, Required DUs Only
# =============================================================================


@pytest.mark.django_db
class TestNonManagedRequiredOnly:
    """Non-managed opportunity with only required deliver units."""

    def test_any_visit_rejected(self):
        """Any visit has status=rejected → CW rejected."""
        opp_access = _setup_non_managed()
        pu = _make_payment_unit(opp_access)
        du = _make_required_du(opp_access, pu)
        cw = _make_cw(opp_access, pu)

        _make_visit(opp_access, du, cw, VisitValidationStatus.approved)
        _make_visit(opp_access, du, cw, VisitValidationStatus.rejected, reason="Invalid data")

        _run_update(cw)

        assert cw.status == CompletedWorkStatus.rejected
        assert "Invalid data" in cw.reason

    def test_1_required_du_1_approved_visit(self):
        """1 required DU, 1 approved visit → approved."""
        opp_access = _setup_non_managed()
        pu = _make_payment_unit(opp_access)
        du = _make_required_du(opp_access, pu)
        cw = _make_cw(opp_access, pu)

        _make_visit(opp_access, du, cw, VisitValidationStatus.approved)

        _run_update(cw)

        assert cw.status == CompletedWorkStatus.approved

    def test_2_required_dus_each_1_approved(self):
        """2 required DUs, each has 1 approved visit → approved."""
        opp_access = _setup_non_managed()
        pu = _make_payment_unit(opp_access)
        du1 = _make_required_du(opp_access, pu)
        du2 = _make_required_du(opp_access, pu)
        cw = _make_cw(opp_access, pu)

        _make_visit(opp_access, du1, cw, VisitValidationStatus.approved)
        _make_visit(opp_access, du2, cw, VisitValidationStatus.approved)

        _run_update(cw)

        assert cw.status == CompletedWorkStatus.approved

    def test_1_required_du_approved_plus_over_limit(self):
        """1 required DU: visits = [approved, over_limit] → approved.
        DU has approved_count=1 > 0, over_limit visit is neutral."""
        opp_access = _setup_non_managed()
        pu = _make_payment_unit(opp_access)
        du = _make_required_du(opp_access, pu)
        cw = _make_cw(opp_access, pu)

        _make_visit(opp_access, du, cw, VisitValidationStatus.approved)
        _make_visit(opp_access, du, cw, VisitValidationStatus.over_limit)

        _run_update(cw)

        assert cw.status == CompletedWorkStatus.approved

    def test_1_required_du_approved_plus_pending(self):
        """1 required DU: visits = [approved, pending] → approved.
        DU has ≥1 approved visit, pending visit is neutral."""
        opp_access = _setup_non_managed()
        pu = _make_payment_unit(opp_access)
        du = _make_required_du(opp_access, pu)
        cw = _make_cw(opp_access, pu)

        _make_visit(opp_access, du, cw, VisitValidationStatus.approved)
        _make_visit(opp_access, du, cw, VisitValidationStatus.pending)

        _run_update(cw)

        assert cw.status == CompletedWorkStatus.approved

    def test_1_required_du_approved_plus_duplicate(self):
        """1 required DU: visits = [approved, duplicate] → approved.
        Duplicate visit is neutral for per-DU check."""
        opp_access = _setup_non_managed()
        pu = _make_payment_unit(opp_access)
        du = _make_required_du(opp_access, pu)
        cw = _make_cw(opp_access, pu)

        _make_visit(opp_access, du, cw, VisitValidationStatus.approved)
        _make_visit(opp_access, du, cw, VisitValidationStatus.duplicate)

        _run_update(cw)

        assert cw.status == CompletedWorkStatus.approved

    def test_2_required_dus_du1_approved_du2_only_pending(self):
        """2 required DUs: DU1 approved, DU2 only pending → unchanged (pending)."""
        opp_access = _setup_non_managed()
        pu = _make_payment_unit(opp_access)
        du1 = _make_required_du(opp_access, pu)
        du2 = _make_required_du(opp_access, pu)
        cw = _make_cw(opp_access, pu)

        _make_visit(opp_access, du1, cw, VisitValidationStatus.approved)
        _make_visit(opp_access, du2, cw, VisitValidationStatus.pending)

        _run_update(cw)

        assert cw.status == CompletedWorkStatus.pending

    def test_incomplete_cw_promoted_to_pending_when_visits_exist(self):
        """CW was incomplete, visits exist but not all approved → promoted to pending."""
        opp_access = _setup_non_managed()
        pu = _make_payment_unit(opp_access)
        du = _make_required_du(opp_access, pu)
        cw = _make_cw(opp_access, pu, status=CompletedWorkStatus.incomplete)

        _make_visit(opp_access, du, cw, VisitValidationStatus.pending)

        _run_update(cw)

        assert cw.status == CompletedWorkStatus.pending

    def test_pending_cw_stays_pending_when_visits_not_all_approved(self):
        """CW was pending, visits not all approved → stays pending."""
        opp_access = _setup_non_managed()
        pu = _make_payment_unit(opp_access)
        du = _make_required_du(opp_access, pu)
        cw = _make_cw(opp_access, pu, status=CompletedWorkStatus.pending)

        _make_visit(opp_access, du, cw, VisitValidationStatus.pending)

        _run_update(cw)

        assert cw.status == CompletedWorkStatus.pending

    def test_rejected_cw_preserved_when_offending_visit_corrected_to_pending(self):
        """CW was rejected, offending visit corrected to pending → stays rejected.
        Terminal status preserved."""
        opp_access = _setup_non_managed()
        pu = _make_payment_unit(opp_access)
        du = _make_required_du(opp_access, pu)
        cw = _make_cw(opp_access, pu, status=CompletedWorkStatus.rejected)

        _make_visit(opp_access, du, cw, VisitValidationStatus.pending)

        _run_update(cw)

        assert cw.status == CompletedWorkStatus.rejected

    def test_rejected_cw_updated_to_approved_when_all_visits_now_approved(self):
        """CW was rejected, all visits corrected to approved → approved."""
        opp_access = _setup_non_managed()
        pu = _make_payment_unit(opp_access)
        du = _make_required_du(opp_access, pu)
        cw = _make_cw(opp_access, pu, status=CompletedWorkStatus.rejected)

        _make_visit(opp_access, du, cw, VisitValidationStatus.approved)

        _run_update(cw)

        assert cw.status == CompletedWorkStatus.approved

    def test_approved_cw_preserved_when_visit_reverts_to_pending(self):
        """CW was approved, a visit reverts to pending → stays approved.
        Terminal status preserved."""
        opp_access = _setup_non_managed()
        pu = _make_payment_unit(opp_access)
        du = _make_required_du(opp_access, pu)
        cw = _make_cw(opp_access, pu, status=CompletedWorkStatus.approved)

        _make_visit(opp_access, du, cw, VisitValidationStatus.pending)

        _run_update(cw)

        assert cw.status == CompletedWorkStatus.approved

    def test_auto_approve_disabled_no_status_change(self):
        """auto_approve_payments=False → no status update regardless of visits."""
        opp_access = _setup_non_managed(auto_approve=False)
        pu = _make_payment_unit(opp_access)
        du = _make_required_du(opp_access, pu)
        cw = _make_cw(opp_access, pu)

        _make_visit(opp_access, du, cw, VisitValidationStatus.approved)

        _run_update(cw)

        assert cw.status == CompletedWorkStatus.pending


# =============================================================================
# Non-managed, Required + Optional DUs
# =============================================================================


@pytest.mark.django_db
class TestNonManagedReqPlusOpt:
    """Non-managed opportunity with required and optional deliver units."""

    def test_any_visit_rejected(self):
        """Any visit rejected → CW rejected. Branch 1 always wins."""
        opp_access = _setup_non_managed()
        pu = _make_payment_unit(opp_access)
        req_du = _make_required_du(opp_access, pu)
        opt_du = _make_optional_du(opp_access, pu)
        cw = _make_cw(opp_access, pu)

        _make_visit(opp_access, req_du, cw, VisitValidationStatus.approved)
        _make_visit(opp_access, opt_du, cw, VisitValidationStatus.rejected, reason="Bad data")

        _run_update(cw)

        assert cw.status == CompletedWorkStatus.rejected

    def test_all_visits_approved_required_and_optional(self):
        """All visits approved (required + optional) → approved."""
        opp_access = _setup_non_managed()
        pu = _make_payment_unit(opp_access)
        req_du = _make_required_du(opp_access, pu)
        opt_du = _make_optional_du(opp_access, pu)
        cw = _make_cw(opp_access, pu)

        _make_visit(opp_access, req_du, cw, VisitValidationStatus.approved)
        _make_visit(opp_access, opt_du, cw, VisitValidationStatus.approved)

        _run_update(cw)

        assert cw.status == CompletedWorkStatus.approved

    def test_all_required_approved_optional_has_approved_plus_over_limit(self):
        """All required approved; ≥1 optional approved; optional also has over_limit → approved.
        Over_limit visit is neutral for per-DU check."""
        opp_access = _setup_non_managed()
        pu = _make_payment_unit(opp_access)
        req_du = _make_required_du(opp_access, pu)
        opt_du = _make_optional_du(opp_access, pu)
        cw = _make_cw(opp_access, pu)

        _make_visit(opp_access, req_du, cw, VisitValidationStatus.approved)
        _make_visit(opp_access, opt_du, cw, VisitValidationStatus.approved)
        _make_visit(opp_access, opt_du, cw, VisitValidationStatus.over_limit)

        _run_update(cw)

        assert cw.status == CompletedWorkStatus.approved

    def test_all_required_approved_no_optional_has_approved_all_pending(self):
        """All required approved; NO optional has approved (all optional visits pending) → not approved.
        Optional check fails (any() over optional DUs = False)."""
        opp_access = _setup_non_managed()
        pu = _make_payment_unit(opp_access)
        req_du = _make_required_du(opp_access, pu)
        opt_du = _make_optional_du(opp_access, pu)
        cw = _make_cw(opp_access, pu)

        _make_visit(opp_access, req_du, cw, VisitValidationStatus.approved)
        _make_visit(opp_access, opt_du, cw, VisitValidationStatus.pending)

        _run_update(cw)

        assert cw.status == CompletedWorkStatus.pending

    def test_all_required_approved_only_approved_visits_no_optional_approved(self):
        """All required approved (only approved visits); no optional has approved visit → not approved.
        Optional DUs exist so any() over optional must find ≥1 approved."""
        opp_access = _setup_non_managed()
        pu = _make_payment_unit(opp_access)
        req_du = _make_required_du(opp_access, pu)
        opt_du = _make_optional_du(opp_access, pu)
        cw = _make_cw(opp_access, pu)

        _make_visit(opp_access, req_du, cw, VisitValidationStatus.approved)
        # optional DU has a pending visit (not approved)
        _make_visit(opp_access, opt_du, cw, VisitValidationStatus.pending)

        _run_update(cw)

        assert cw.status == CompletedWorkStatus.pending

    def test_all_required_approved_optional_du_has_no_visits_at_all(self):
        """All required approved (only approved visits); optional DU has no approved visits → not approved.
        Optional DUs exist but any() = False → not approved.
        Note: we add a pending visit on the optional DU to bypass the completed_count guard
        and actually exercise the approval logic."""
        opp_access = _setup_non_managed()
        pu = _make_payment_unit(opp_access)
        req_du = _make_required_du(opp_access, pu)
        opt_du = _make_optional_du(opp_access, pu)
        cw = _make_cw(opp_access, pu)

        _make_visit(opp_access, req_du, cw, VisitValidationStatus.approved)
        _make_visit(opp_access, opt_du, cw, VisitValidationStatus.pending)  # bypass count guard

        _run_update(cw)

        # CW should NOT be approved because optional DU has no approved visits
        assert cw.status == CompletedWorkStatus.pending

    def test_incomplete_cw_with_required_and_optional_approved(self):
        """CW was incomplete; required approved; ≥1 optional approved → approved."""
        opp_access = _setup_non_managed()
        pu = _make_payment_unit(opp_access)
        req_du = _make_required_du(opp_access, pu)
        opt_du = _make_optional_du(opp_access, pu)
        cw = _make_cw(opp_access, pu, status=CompletedWorkStatus.incomplete)

        _make_visit(opp_access, req_du, cw, VisitValidationStatus.approved)
        _make_visit(opp_access, opt_du, cw, VisitValidationStatus.approved)

        _run_update(cw)

        assert cw.status == CompletedWorkStatus.approved

    def test_incomplete_cw_promoted_to_pending_when_optional_not_approved(self):
        """CW was incomplete; required approved but optional not → pending (promoted from incomplete)."""
        opp_access = _setup_non_managed()
        pu = _make_payment_unit(opp_access)
        req_du = _make_required_du(opp_access, pu)
        opt_du = _make_optional_du(opp_access, pu)
        cw = _make_cw(opp_access, pu, status=CompletedWorkStatus.incomplete)

        _make_visit(opp_access, req_du, cw, VisitValidationStatus.approved)
        _make_visit(opp_access, opt_du, cw, VisitValidationStatus.pending)

        _run_update(cw)

        assert cw.status == CompletedWorkStatus.pending

    def test_incomplete_cw_updates_when_only_required_du_has_visit(self):
        opp_access = _setup_non_managed()
        pu = _make_payment_unit(opp_access)
        req_du = _make_required_du(opp_access, pu)
        _make_optional_du(opp_access, pu)  # exists but has no visits
        cw = _make_cw(opp_access, pu, status=CompletedWorkStatus.incomplete)

        _make_visit(opp_access, req_du, cw, VisitValidationStatus.pending)

        _run_update(cw)

        assert cw.status == CompletedWorkStatus.pending

    def test_incomplete_cw_updates_when_only_required_du_approved(self):
        opp_access = _setup_non_managed()
        pu = _make_payment_unit(opp_access)
        req_du = _make_required_du(opp_access, pu)
        _make_optional_du(opp_access, pu)
        cw = _make_cw(opp_access, pu, status=CompletedWorkStatus.incomplete)

        _make_visit(opp_access, req_du, cw, VisitValidationStatus.approved)

        _run_update(cw)

        assert cw.status == CompletedWorkStatus.pending

    def test_rejected_visit_on_required_du_no_optional_visits(self):
        opp_access = _setup_non_managed()
        pu = _make_payment_unit(opp_access)
        req_du = _make_required_du(opp_access, pu)
        _make_optional_du(opp_access, pu)
        cw = _make_cw(opp_access, pu, status=CompletedWorkStatus.pending)

        _make_visit(opp_access, req_du, cw, VisitValidationStatus.rejected, reason="Bad data")

        _run_update(cw)

        assert cw.status == CompletedWorkStatus.rejected

    def test_payment_not_calculated_until_optional_du_submitted(self):
        opp_access = _setup_non_managed()
        pu = _make_payment_unit(opp_access, amount=100)
        req_du = _make_required_du(opp_access, pu)
        _make_optional_du(opp_access, pu)
        cw = _make_cw(opp_access, pu, status=CompletedWorkStatus.incomplete)

        _make_visit(opp_access, req_du, cw, VisitValidationStatus.approved)

        _run_update(cw)

        assert cw.status == CompletedWorkStatus.pending
        assert cw.saved_payment_accrued == 0
        assert cw.saved_completed_count == 0


# =============================================================================
# Managed, Required DUs Only
# =============================================================================


@pytest.mark.django_db
class TestManagedRequiredOnly:
    """Managed opportunity with only required deliver units."""

    def test_any_visit_rejected(self):
        """Any visit rejected → CW rejected. review_status irrelevant."""
        opp_access = _setup_managed()
        pu = _make_payment_unit(opp_access)
        du = _make_required_du(opp_access, pu)
        cw = _make_cw(opp_access, pu)

        _make_visit(opp_access, du, cw, VisitValidationStatus.rejected, reason="Bad")

        _run_update(cw)

        assert cw.status == CompletedWorkStatus.rejected

    def test_all_approved_all_agree(self):
        """All visits approved + all review_status=agree → approved."""
        opp_access = _setup_managed()
        pu = _make_payment_unit(opp_access)
        du = _make_required_du(opp_access, pu)
        cw = _make_cw(opp_access, pu)

        _make_visit(opp_access, du, cw, VisitValidationStatus.approved, review_status=VisitReviewStatus.agree)

        _run_update(cw)

        assert cw.status == CompletedWorkStatus.approved

    def test_all_approved_none_agree_review_pending(self):
        """All visits approved; none have agree (review pending) → not approved.
        Per-DU agree=0 → not approved → stays current status."""
        opp_access = _setup_managed()
        pu = _make_payment_unit(opp_access)
        du = _make_required_du(opp_access, pu)
        cw = _make_cw(opp_access, pu)

        _make_visit(opp_access, du, cw, VisitValidationStatus.approved, review_status=VisitReviewStatus.pending)

        _run_update(cw)

        assert cw.status == CompletedWorkStatus.pending

    def test_approved_plus_agree_and_over_limit(self):
        """1 required DU: visits = [approved+agree, over_limit] → approved.
        DU has approved+agree, over_limit visit is neutral."""
        opp_access = _setup_managed()
        pu = _make_payment_unit(opp_access)
        du = _make_required_du(opp_access, pu)
        cw = _make_cw(opp_access, pu)

        _make_visit(opp_access, du, cw, VisitValidationStatus.approved, review_status=VisitReviewStatus.agree)
        _make_visit(opp_access, du, cw, VisitValidationStatus.over_limit)

        _run_update(cw)

        assert cw.status == CompletedWorkStatus.approved

    def test_all_approved_mixed_reviews_some_agree_some_pending(self):
        """All visits approved; mixed reviews: some agree, some pending.
        Per-DU check — DU has agree > 0 from one visit → approved."""
        opp_access = _setup_managed()
        pu = _make_payment_unit(opp_access)
        du = _make_required_du(opp_access, pu)
        cw = _make_cw(opp_access, pu)

        _make_visit(opp_access, du, cw, VisitValidationStatus.approved, review_status=VisitReviewStatus.agree)
        _make_visit(opp_access, du, cw, VisitValidationStatus.approved, review_status=VisitReviewStatus.pending)

        _run_update(cw)

        assert cw.status == CompletedWorkStatus.approved

    def test_2_required_dus_mixed_reviews_one_du_no_agree(self):
        """2 required DUs: DU1 has approved+agree, DU2 has approved+pending_review → not approved.
        Per-DU: DU2 has agree=0 → all_required_approved fails."""
        opp_access = _setup_managed()
        pu = _make_payment_unit(opp_access)
        du1 = _make_required_du(opp_access, pu)
        du2 = _make_required_du(opp_access, pu)
        cw = _make_cw(opp_access, pu)

        _make_visit(opp_access, du1, cw, VisitValidationStatus.approved, review_status=VisitReviewStatus.agree)
        _make_visit(opp_access, du2, cw, VisitValidationStatus.approved, review_status=VisitReviewStatus.pending)

        _run_update(cw)

        assert cw.status == CompletedWorkStatus.pending

    def test_rejected_cw_all_approved_not_all_agree(self):
        """CW was rejected; all visits now approved; not all agree → rejected preserved.
        Per-DU agree=0 → not approved → incomplete promotion doesn't apply → rejected preserved."""
        opp_access = _setup_managed()
        pu = _make_payment_unit(opp_access)
        du = _make_required_du(opp_access, pu)
        cw = _make_cw(opp_access, pu, status=CompletedWorkStatus.rejected)

        _make_visit(opp_access, du, cw, VisitValidationStatus.approved, review_status=VisitReviewStatus.pending)

        _run_update(cw)

        assert cw.status == CompletedWorkStatus.rejected

    def test_approved_cw_preserved_when_reviewer_changes_agree_to_pending(self):
        """CW was approved; reviewer changes agree → pending → stays approved.
        Terminal status preserved."""
        opp_access = _setup_managed()
        pu = _make_payment_unit(opp_access)
        du = _make_required_du(opp_access, pu)
        cw = _make_cw(opp_access, pu, status=CompletedWorkStatus.approved)

        _make_visit(opp_access, du, cw, VisitValidationStatus.approved, review_status=VisitReviewStatus.pending)

        _run_update(cw)

        assert cw.status == CompletedWorkStatus.approved

    def test_incomplete_cw_promoted_to_pending_not_all_agree(self):
        """CW was incomplete; all visits approved but not agreed → pending (promoted from incomplete)."""
        opp_access = _setup_managed()
        pu = _make_payment_unit(opp_access)
        du = _make_required_du(opp_access, pu)
        cw = _make_cw(opp_access, pu, status=CompletedWorkStatus.incomplete)

        _make_visit(opp_access, du, cw, VisitValidationStatus.approved, review_status=VisitReviewStatus.pending)

        _run_update(cw)

        assert cw.status == CompletedWorkStatus.pending

    def test_rejected_cw_updated_to_approved_when_all_approved_and_agreed(self):
        """CW was rejected; all visits now approved+agree → approved."""
        opp_access = _setup_managed()
        pu = _make_payment_unit(opp_access)
        du = _make_required_du(opp_access, pu)
        cw = _make_cw(opp_access, pu, status=CompletedWorkStatus.rejected)

        _make_visit(opp_access, du, cw, VisitValidationStatus.approved, review_status=VisitReviewStatus.agree)

        _run_update(cw)

        assert cw.status == CompletedWorkStatus.approved


# =============================================================================
# Managed, Required + Optional DUs
# =============================================================================


@pytest.mark.django_db
class TestManagedReqPlusOpt:
    """Managed opportunity with required and optional deliver units."""

    def test_any_visit_rejected(self):
        """Any visit rejected → CW rejected. Branch 1 wins."""
        opp_access = _setup_managed()
        pu = _make_payment_unit(opp_access)
        req_du = _make_required_du(opp_access, pu)
        opt_du = _make_optional_du(opp_access, pu)
        cw = _make_cw(opp_access, pu)

        _make_visit(opp_access, req_du, cw, VisitValidationStatus.approved, review_status=VisitReviewStatus.agree)
        _make_visit(opp_access, opt_du, cw, VisitValidationStatus.rejected, reason="Bad")

        _run_update(cw)

        assert cw.status == CompletedWorkStatus.rejected

    def test_all_approved_and_agree_required_and_optional(self):
        """All visits approved + agree (required + optional) → approved."""
        opp_access = _setup_managed()
        pu = _make_payment_unit(opp_access)
        req_du = _make_required_du(opp_access, pu)
        opt_du = _make_optional_du(opp_access, pu)
        cw = _make_cw(opp_access, pu)

        _make_visit(opp_access, req_du, cw, VisitValidationStatus.approved, review_status=VisitReviewStatus.agree)
        _make_visit(opp_access, opt_du, cw, VisitValidationStatus.approved, review_status=VisitReviewStatus.agree)

        _run_update(cw)

        assert cw.status == CompletedWorkStatus.approved

    def test_required_approved_agree_optional_approved_agree_plus_over_limit(self):
        """Required: approved+agree. Optional: approved+agree + extra over_limit → approved.
        Over_limit visit is neutral for per-DU check."""
        opp_access = _setup_managed()
        pu = _make_payment_unit(opp_access)
        req_du = _make_required_du(opp_access, pu)
        opt_du = _make_optional_du(opp_access, pu)
        cw = _make_cw(opp_access, pu)

        _make_visit(opp_access, req_du, cw, VisitValidationStatus.approved, review_status=VisitReviewStatus.agree)
        _make_visit(opp_access, opt_du, cw, VisitValidationStatus.approved, review_status=VisitReviewStatus.agree)
        _make_visit(opp_access, opt_du, cw, VisitValidationStatus.over_limit)

        _run_update(cw)

        assert cw.status == CompletedWorkStatus.approved

    def test_required_approved_agree_optional_approved_review_pending(self):
        """Required: approved+agree. Optional: approved but review pending → not approved.
        Optional DU agree=0 → optional check fails → not approved."""
        opp_access = _setup_managed()
        pu = _make_payment_unit(opp_access)
        req_du = _make_required_du(opp_access, pu)
        opt_du = _make_optional_du(opp_access, pu)
        cw = _make_cw(opp_access, pu)

        _make_visit(opp_access, req_du, cw, VisitValidationStatus.approved, review_status=VisitReviewStatus.agree)
        _make_visit(opp_access, opt_du, cw, VisitValidationStatus.approved, review_status=VisitReviewStatus.pending)

        _run_update(cw)

        assert cw.status == CompletedWorkStatus.pending

    def test_required_agree_one_optional_agree_another_optional_only_approved(self):
        """Required: approved+agree. Optional1: approved+agree. Optional2: approved only → approved.
        any() over optionals — Optional1 passes → approved."""
        opp_access = _setup_managed()
        pu = _make_payment_unit(opp_access)
        req_du = _make_required_du(opp_access, pu)
        opt_du1 = _make_optional_du(opp_access, pu)
        opt_du2 = _make_optional_du(opp_access, pu)
        cw = _make_cw(opp_access, pu)

        _make_visit(opp_access, req_du, cw, VisitValidationStatus.approved, review_status=VisitReviewStatus.agree)
        _make_visit(opp_access, opt_du1, cw, VisitValidationStatus.approved, review_status=VisitReviewStatus.agree)
        _make_visit(opp_access, opt_du2, cw, VisitValidationStatus.approved, review_status=VisitReviewStatus.pending)

        _run_update(cw)

        assert cw.status == CompletedWorkStatus.approved

    def test_required_agree_optional_no_approved_visits(self):
        """Required: approved+agree. Optional: no approved visits → not approved.
        any() = False → not approved.
        Note: we add a pending visit on the optional DU to bypass the completed_count guard
        and actually exercise the approval logic."""
        opp_access = _setup_managed()
        pu = _make_payment_unit(opp_access)
        req_du = _make_required_du(opp_access, pu)
        opt_du = _make_optional_du(opp_access, pu)
        cw = _make_cw(opp_access, pu)

        _make_visit(opp_access, req_du, cw, VisitValidationStatus.approved, review_status=VisitReviewStatus.agree)
        _make_visit(opp_access, opt_du, cw, VisitValidationStatus.pending)  # bypass count guard

        _run_update(cw)

        assert cw.status == CompletedWorkStatus.pending

    def test_required_agree_optional_approved_disagree(self):
        """Required: approved+agree. Optional: approved+disagree → not approved.
        Optional agree=0 → fails."""
        opp_access = _setup_managed()
        pu = _make_payment_unit(opp_access)
        req_du = _make_required_du(opp_access, pu)
        opt_du = _make_optional_du(opp_access, pu)
        cw = _make_cw(opp_access, pu)

        _make_visit(opp_access, req_du, cw, VisitValidationStatus.approved, review_status=VisitReviewStatus.agree)
        _make_visit(opp_access, opt_du, cw, VisitValidationStatus.approved, review_status=VisitReviewStatus.disagree)

        _run_update(cw)

        assert cw.status == CompletedWorkStatus.pending

    def test_not_all_required_satisfied_optional_has_approved_agree(self):
        """Not all required DUs satisfied; optional has approved+agree → not approved.
        Required DUs must all pass first."""
        opp_access = _setup_managed()
        pu = _make_payment_unit(opp_access)
        req_du1 = _make_required_du(opp_access, pu)
        req_du2 = _make_required_du(opp_access, pu)
        opt_du = _make_optional_du(opp_access, pu)
        cw = _make_cw(opp_access, pu)

        _make_visit(opp_access, req_du1, cw, VisitValidationStatus.approved, review_status=VisitReviewStatus.agree)
        _make_visit(opp_access, req_du2, cw, VisitValidationStatus.pending)
        _make_visit(opp_access, opt_du, cw, VisitValidationStatus.approved, review_status=VisitReviewStatus.agree)

        _run_update(cw)

        assert cw.status == CompletedWorkStatus.pending

    def test_incomplete_cw_not_all_required_promoted_to_pending(self):
        """CW was incomplete; not all required satisfied; optional approved+agree → pending."""
        opp_access = _setup_managed()
        pu = _make_payment_unit(opp_access)
        req_du1 = _make_required_du(opp_access, pu)
        req_du2 = _make_required_du(opp_access, pu)
        opt_du = _make_optional_du(opp_access, pu)
        cw = _make_cw(opp_access, pu, status=CompletedWorkStatus.incomplete)

        _make_visit(opp_access, req_du1, cw, VisitValidationStatus.approved, review_status=VisitReviewStatus.agree)
        _make_visit(opp_access, req_du2, cw, VisitValidationStatus.pending)
        _make_visit(opp_access, opt_du, cw, VisitValidationStatus.approved, review_status=VisitReviewStatus.agree)

        _run_update(cw)

        assert cw.status == CompletedWorkStatus.pending

    def test_managed_incomplete_cw_required_approved_agree_no_optional_visits(self):
        opp_access = _setup_managed()
        pu = _make_payment_unit(opp_access)
        req_du = _make_required_du(opp_access, pu)
        _make_optional_du(opp_access, pu)
        cw = _make_cw(opp_access, pu, status=CompletedWorkStatus.incomplete)

        _make_visit(opp_access, req_du, cw, VisitValidationStatus.approved, review_status=VisitReviewStatus.agree)

        _run_update(cw)

        assert cw.status == CompletedWorkStatus.pending

    def test_managed_payment_not_calculated_until_optional_du_submitted(self):
        opp_access = _setup_managed()
        pu = _make_payment_unit(opp_access, amount=100)
        req_du = _make_required_du(opp_access, pu)
        _make_optional_du(opp_access, pu)
        cw = _make_cw(opp_access, pu, status=CompletedWorkStatus.incomplete)

        _make_visit(opp_access, req_du, cw, VisitValidationStatus.approved)

        _run_update(cw)

        assert cw.status == CompletedWorkStatus.pending
        assert cw.saved_payment_accrued == 0
        assert cw.saved_completed_count == 0


# =============================================================================
# EDGE CASES
# =============================================================================


@pytest.mark.django_db
class TestEdgeCases:
    def test_over_limit_visit_neutral_for_status(self):
        """over_limit visits: not rejected, not approved. Effectively neutral.
        Does increment unit_counts but doesn't contribute to approved counts."""
        opp_access = _setup_non_managed()
        pu = _make_payment_unit(opp_access)
        du = _make_required_du(opp_access, pu)
        cw = _make_cw(opp_access, pu)

        _make_visit(opp_access, du, cw, VisitValidationStatus.approved)
        _make_visit(opp_access, du, cw, VisitValidationStatus.over_limit)

        _run_update(cw)

        assert cw.status == CompletedWorkStatus.approved
        assert cw.saved_completed_count == 2  # both count toward completed
        assert cw.saved_approved_count == 1  # only approved counts

    def test_duplicate_visit_neutral_for_status(self):
        """duplicate visits: same as over_limit — neutral for status."""
        opp_access = _setup_non_managed()
        pu = _make_payment_unit(opp_access)
        du = _make_required_du(opp_access, pu)
        cw = _make_cw(opp_access, pu)

        _make_visit(opp_access, du, cw, VisitValidationStatus.approved)
        _make_visit(opp_access, du, cw, VisitValidationStatus.duplicate)

        _run_update(cw)

        assert cw.status == CompletedWorkStatus.approved
        assert cw.saved_completed_count == 2
        assert cw.saved_approved_count == 1

    def test_trial_visit_neutral_for_status(self):
        """trial visits: same as over_limit/duplicate — neutral for status."""
        opp_access = _setup_non_managed()
        pu = _make_payment_unit(opp_access)
        du = _make_required_du(opp_access, pu)
        cw = _make_cw(opp_access, pu)

        _make_visit(opp_access, du, cw, VisitValidationStatus.approved)
        _make_visit(opp_access, du, cw, VisitValidationStatus.trial)

        _run_update(cw)

        assert cw.status == CompletedWorkStatus.approved
        assert cw.saved_completed_count == 2
        assert cw.saved_approved_count == 1

    def test_no_required_no_optional_deliver_units_vacuous_approval(self):
        """No required DUs and no optional DUs → vacuous approval.
        all([]) = True and no optionals → returns True → approved."""
        opp_access = _setup_non_managed()
        pu = _make_payment_unit(opp_access)
        # No deliver units created for this payment unit
        cw = _make_cw(opp_access, pu)

        # Need at least one visit for the CW to be processed (completed_count guard)
        # But with no deliver units, unit_counts will be empty, and min([], default=0) = 0
        # So completed_count = 0 and the CW will be skipped by the guard.
        # This means the vacuous approval in _is_completed_work_approved is unreachable
        # when there are truly zero DUs, because the count guard prevents it.
        _run_update(cw)

        # With no deliver units, completed_count=0, CW is skipped entirely
        assert cw.status == CompletedWorkStatus.pending

    def test_no_required_dus_with_optional_approved(self):
        """No required DUs, optional DU with approved visit → approved.
        all([]) = True for required (vacuous). any() finds approved optional."""
        opp_access = _setup_non_managed()
        pu = _make_payment_unit(opp_access)
        opt_du = _make_optional_du(opp_access, pu)
        cw = _make_cw(opp_access, pu)

        _make_visit(opp_access, opt_du, cw, VisitValidationStatus.approved)

        _run_update(cw)

        assert cw.status == CompletedWorkStatus.approved

    def test_multiple_visits_same_required_du_1_approved_1_pending(self):
        """Multiple visits on same required DU: 1 approved + 1 pending.
        Only needs ≥1 approved per DU → approved."""
        opp_access = _setup_non_managed()
        pu = _make_payment_unit(opp_access)
        du = _make_required_du(opp_access, pu)
        cw = _make_cw(opp_access, pu)

        _make_visit(opp_access, du, cw, VisitValidationStatus.approved)
        _make_visit(opp_access, du, cw, VisitValidationStatus.pending)

        _run_update(cw)

        assert cw.status == CompletedWorkStatus.approved

    def test_managed_agreement_per_du_not_global(self):
        """Managed: DU has both agree and non-agree visit. DU still passes (only needs ≥1 agree)."""
        opp_access = _setup_managed()
        pu = _make_payment_unit(opp_access)
        du = _make_required_du(opp_access, pu)
        cw = _make_cw(opp_access, pu)

        _make_visit(opp_access, du, cw, VisitValidationStatus.approved, review_status=VisitReviewStatus.agree)
        _make_visit(opp_access, du, cw, VisitValidationStatus.approved, review_status=VisitReviewStatus.pending)
        _make_visit(opp_access, du, cw, VisitValidationStatus.approved, review_status=VisitReviewStatus.disagree)

        _run_update(cw)

        assert cw.status == CompletedWorkStatus.approved

    def test_rejection_reason_concatenated_from_all_visits(self):
        """When rejected, reason is concatenated from all visits with reasons."""
        opp_access = _setup_non_managed()
        pu = _make_payment_unit(opp_access)
        du = _make_required_du(opp_access, pu)
        cw = _make_cw(opp_access, pu)

        _make_visit(opp_access, du, cw, VisitValidationStatus.rejected, reason="Reason A")
        _make_visit(opp_access, du, cw, VisitValidationStatus.rejected, reason="Reason B")

        _run_update(cw)

        assert cw.status == CompletedWorkStatus.rejected
        assert "Reason A" in cw.reason
        assert "Reason B" in cw.reason


# =============================================================================
# PAYMENT CALCULATIONS
# =============================================================================


@pytest.mark.django_db
class TestPaymentCalculations:
    """Verify payment fields are correctly set based on status."""

    def test_payment_accrued_when_approved(self):
        """Approved CW → payment = approved_count * amount."""
        opp_access = _setup_non_managed()
        pu = _make_payment_unit(opp_access, amount=150)
        du = _make_required_du(opp_access, pu)
        cw = _make_cw(opp_access, pu)

        for _ in range(3):
            _make_visit(opp_access, du, cw, VisitValidationStatus.approved)

        _run_update(cw)

        assert cw.status == CompletedWorkStatus.approved
        assert cw.saved_approved_count == 3
        assert cw.saved_completed_count == 3
        assert cw.saved_payment_accrued == 450
        assert cw.saved_payment_accrued_usd > 0

    def test_zero_payment_when_not_approved(self):
        """Pending CW → payment = 0 even if some visits are approved."""
        opp_access = _setup_non_managed()
        pu = _make_payment_unit(opp_access, amount=100)
        du1 = _make_required_du(opp_access, pu)
        du2 = _make_required_du(opp_access, pu)
        cw = _make_cw(opp_access, pu)

        _make_visit(opp_access, du1, cw, VisitValidationStatus.approved)
        _make_visit(opp_access, du2, cw, VisitValidationStatus.pending)

        _run_update(cw)

        assert cw.status == CompletedWorkStatus.pending
        assert cw.saved_payment_accrued == 0

    def test_zero_payment_when_rejected(self):
        """Rejected CW → payment = 0."""
        opp_access = _setup_non_managed()
        pu = _make_payment_unit(opp_access, amount=100)
        du = _make_required_du(opp_access, pu)
        cw = _make_cw(opp_access, pu)

        _make_visit(opp_access, du, cw, VisitValidationStatus.rejected, reason="Bad")

        _run_update(cw)

        assert cw.status == CompletedWorkStatus.rejected
        assert cw.saved_payment_accrued == 0

    def test_zero_payment_when_auto_approve_disabled(self):
        """auto_approve=False → payment = 0 even with approved visits."""
        opp_access = _setup_non_managed(auto_approve=False)
        pu = _make_payment_unit(opp_access, amount=100)
        du = _make_required_du(opp_access, pu)
        cw = _make_cw(opp_access, pu)

        _make_visit(opp_access, du, cw, VisitValidationStatus.approved)

        _run_update(cw)

        assert cw.status == CompletedWorkStatus.pending
        assert cw.saved_payment_accrued == 0

    def test_approved_count_stored_even_when_status_not_approved(self):
        """Counts are stored regardless of status, but payment is only calculated when approved."""
        opp_access = _setup_managed()
        pu = _make_payment_unit(opp_access, amount=100)
        req_du = _make_required_du(opp_access, pu)
        cw = _make_cw(opp_access, pu)

        _make_visit(opp_access, req_du, cw, VisitValidationStatus.approved, review_status=VisitReviewStatus.pending)

        _run_update(cw)

        assert cw.status == CompletedWorkStatus.pending
        assert cw.saved_approved_count == 1  # count stored
        assert cw.saved_completed_count == 1  # count stored
        assert cw.saved_payment_accrued == 0  # but no payment


@pytest.mark.django_db
class TestParentChildPaymentUnit:
    """Tests for parent-child PaymentUnit relationships processed in the same batch."""

    def test_parent_processed_before_child_both_approved(self):
        opp_access = _setup_non_managed()

        parent_pu = _make_payment_unit(opp_access)
        parent_du = _make_required_du(opp_access, parent_pu)

        child_pu = PaymentUnitFactory(
            opportunity=opp_access.opportunity,
            amount=100,
            parent_payment_unit=parent_pu,
        )
        child_du = _make_required_du(opp_access, child_pu)

        entity_id = "test-entity-001"

        # Parent CW created first → lower DB id → comes first when ordered by id
        parent_cw = CompletedWorkFactory(
            status=CompletedWorkStatus.pending,
            opportunity_access=opp_access,
            payment_unit=parent_pu,
            entity_id=entity_id,
        )
        child_cw = CompletedWorkFactory(
            status=CompletedWorkStatus.pending,
            opportunity_access=opp_access,
            payment_unit=child_pu,
            entity_id=entity_id,
        )

        _make_visit(opp_access, parent_du, parent_cw, VisitValidationStatus.approved)
        _make_visit(opp_access, child_du, child_cw, VisitValidationStatus.approved)

        # Process both in one batch with parent ordered first (ascending id)
        completed_works = (
            CompletedWork.objects.filter(id__in=[parent_cw.id, child_cw.id])
            .order_by("id")
            .select_related("payment_unit")
        )
        update_status(completed_works, opp_access, compute_payment=True)

        parent_cw.refresh_from_db()
        child_cw.refresh_from_db()

        assert parent_cw.status == CompletedWorkStatus.approved
        assert child_cw.status == CompletedWorkStatus.approved

    def test_child_processed_before_parent_both_approved(self):
        opp_access = _setup_non_managed()

        parent_pu = _make_payment_unit(opp_access)
        parent_du = _make_required_du(opp_access, parent_pu)

        child_pu = PaymentUnitFactory(
            opportunity=opp_access.opportunity,
            amount=100,
            parent_payment_unit=parent_pu,
        )
        child_du = _make_required_du(opp_access, child_pu)

        entity_id = "test-entity-002"

        parent_cw = CompletedWorkFactory(
            status=CompletedWorkStatus.pending,
            opportunity_access=opp_access,
            payment_unit=parent_pu,
            entity_id=entity_id,
        )
        child_cw = CompletedWorkFactory(
            status=CompletedWorkStatus.pending,
            opportunity_access=opp_access,
            payment_unit=child_pu,
            entity_id=entity_id,
        )

        _make_visit(opp_access, parent_du, parent_cw, VisitValidationStatus.approved)
        _make_visit(opp_access, child_du, child_cw, VisitValidationStatus.approved)

        # Process both in one batch with child ordered first (descending id)
        completed_works = (
            CompletedWork.objects.filter(id__in=[parent_cw.id, child_cw.id])
            .order_by("-id")
            .select_related("payment_unit")
        )
        update_status(completed_works, opp_access, compute_payment=True)

        parent_cw.refresh_from_db()
        child_cw.refresh_from_db()

        assert parent_cw.status == CompletedWorkStatus.approved
        assert child_cw.status == CompletedWorkStatus.approved
