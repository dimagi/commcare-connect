import pytest
from django.core.management import CommandError, call_command

from commcare_connect.microplanning.models import WorkAreaStatus
from commcare_connect.microplanning.tests.factories import WorkAreaFactory
from commcare_connect.opportunity.management.commands import approve_over_limit_visits
from commcare_connect.opportunity.models import CompletedWorkStatus, VisitReviewStatus, VisitValidationStatus
from commcare_connect.opportunity.tests.factories import (
    CompletedWorkFactory,
    DeliverUnitFactory,
    OpportunityAccessFactory,
    PaymentUnitFactory,
    UserVisitFactory,
)


@pytest.fixture
def access(opportunity):
    return OpportunityAccessFactory(opportunity=opportunity)


@pytest.fixture
def over_limit_visit(opportunity, access):
    payment_unit = PaymentUnitFactory(opportunity=opportunity)
    completed_work = CompletedWorkFactory(
        opportunity_access=access, payment_unit=payment_unit, status=CompletedWorkStatus.over_limit
    )
    return UserVisitFactory(
        opportunity=opportunity,
        user=access.user,
        opportunity_access=access,
        deliver_unit=DeliverUnitFactory(payment_unit=payment_unit),
        completed_work=completed_work,
        status=VisitValidationStatus.over_limit,
        review_status=VisitReviewStatus.pending,
        flagged=False,
    )


def _approve(opportunity, visits, *args):
    call_command(
        "approve_over_limit_visits",
        "--opportunity",
        str(opportunity.opportunity_id),
        "--visit-ids",
        *[str(visit.id) for visit in visits],
        *args,
    )


@pytest.mark.django_db
def test_dry_run_saves_nothing(opportunity, over_limit_visit):
    _approve(opportunity, [over_limit_visit])

    over_limit_visit.refresh_from_db()
    assert over_limit_visit.status == VisitValidationStatus.over_limit


@pytest.mark.django_db
def test_approves_only_over_limit_visits_for_pm_review(opportunity, access, over_limit_visit):
    work_area = WorkAreaFactory(
        opportunity=opportunity, opportunity_access=access, status=WorkAreaStatus.VISITED, expected_visit_count=1
    )
    over_limit_visit.work_area = work_area
    over_limit_visit.save()
    pending_visit = UserVisitFactory(
        opportunity=opportunity, opportunity_access=access, status=VisitValidationStatus.pending
    )
    other_opportunity_visit = UserVisitFactory(status=VisitValidationStatus.over_limit)

    _approve(opportunity, [over_limit_visit, pending_visit, other_opportunity_visit], "--commit")

    over_limit_visit.refresh_from_db()
    assert over_limit_visit.status == VisitValidationStatus.approved
    assert over_limit_visit.review_status == VisitReviewStatus.pending
    assert over_limit_visit.review_created_on
    work_area.refresh_from_db()
    assert work_area.status == WorkAreaStatus.EXPECTED_VISIT_REACHED
    pending_visit.refresh_from_db()
    assert pending_visit.status == VisitValidationStatus.pending
    other_opportunity_visit.refresh_from_db()
    assert other_opportunity_visit.status == VisitValidationStatus.over_limit


@pytest.mark.django_db
def test_flagged_visit_gets_justification(opportunity, over_limit_visit):
    over_limit_visit.flagged = True
    over_limit_visit.save()

    _approve(opportunity, [over_limit_visit], "--commit")

    over_limit_visit.refresh_from_db()
    assert over_limit_visit.status == VisitValidationStatus.approved
    assert over_limit_visit.justification == approve_over_limit_visits.JUSTIFICATION


@pytest.mark.django_db
@pytest.mark.parametrize("justification", ["", "x" * 301])
def test_unusable_justification_saves_nothing(monkeypatch, opportunity, over_limit_visit, justification):
    monkeypatch.setattr(approve_over_limit_visits, "JUSTIFICATION", justification)

    with pytest.raises(CommandError, match="JUSTIFICATION"):
        _approve(opportunity, [over_limit_visit], "--commit")

    over_limit_visit.refresh_from_db()
    assert over_limit_visit.status == VisitValidationStatus.over_limit


@pytest.mark.django_db
def test_agree_refused_on_manual_verification(opportunity, over_limit_visit):
    with pytest.raises(CommandError, match="automatic visit verification"):
        _approve(opportunity, [over_limit_visit], "--commit", "--agree")


@pytest.mark.django_db
def test_agree_approves_unflagged_visits_and_accrues_payment(opportunity, access, over_limit_visit):
    opportunity.automatic_visit_verification = True
    opportunity.auto_approve_payments = True
    opportunity.save()
    flagged_visit = UserVisitFactory(
        opportunity=opportunity, opportunity_access=access, status=VisitValidationStatus.over_limit, flagged=True
    )

    _approve(opportunity, [over_limit_visit, flagged_visit], "--commit", "--agree")

    over_limit_visit.refresh_from_db()
    assert over_limit_visit.status == VisitValidationStatus.approved
    assert over_limit_visit.review_status == VisitReviewStatus.agree
    over_limit_visit.completed_work.refresh_from_db()
    assert over_limit_visit.completed_work.status == CompletedWorkStatus.approved
    access.refresh_from_db()
    assert access.payment_accrued == over_limit_visit.completed_work.payment_unit.amount
    flagged_visit.refresh_from_db()
    assert flagged_visit.status == VisitValidationStatus.over_limit
