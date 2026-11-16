"""What happens to a visit and its completed work when a suspended worker submits a deliver form.

The receiver always rejects a suspended worker's visit. What happens to the completed work depends on
whether the opportunity approves payments automatically, whether the work was already approved, and
whether the visit goes over the daily limit.
"""

from copy import deepcopy
from functools import partial
from uuid import uuid4

import pytest

from commcare_connect.form_receiver.tests.test_receiver_integration import _create_opp_and_form_json, make_request
from commcare_connect.opportunity.models import (
    CompletedWork,
    CompletedWorkStatus,
    OpportunityAccess,
    UserVisit,
    VisitValidationStatus,
)
from commcare_connect.opportunity.visit_import import update_payment_accrued


class TestWithAutoPayments:
    """The payment recalculation rejects the work whenever one of its visits is rejected,
    including work that was already approved."""

    def test_new_work_is_rejected(self, submit_suspended_visit):
        """Case 1: A suspended worker's first visit to an entity.

        The payment recalculation sees the rejected visit and rejects the work.
        """
        visit = submit_suspended_visit(auto_approve_payments=True, work_approved=False)
        _assert_work(visit=visit, status=CompletedWorkStatus.rejected, paid=False)

    def test_approved_work_is_rejected_and_unpaid(self, submit_suspended_visit):
        """Case 2: A suspended worker visits an entity they already have approved completed work for.

        The payment recalculation rejects the work because one of its visits is rejected,
        which takes back the pay the worker had earned for it.
        """
        visit = submit_suspended_visit(auto_approve_payments=True, work_approved=True)
        _assert_work(visit=visit, status=CompletedWorkStatus.rejected, paid=False)

    def test_over_limit_approved_work_is_rejected_and_unpaid(self, submit_suspended_visit):
        """Case 3: A suspended worker visits an entity they already have approved completed work for,
        and the visit goes over the daily limit.

        The over-limit check leaves approved work alone, but the payment recalculation rejects the
        work because one of its visits is rejected, which takes back the pay the worker had earned.
        """
        visit = submit_suspended_visit(auto_approve_payments=True, work_approved=True, over_limit=True)
        _assert_work(visit=visit, status=CompletedWorkStatus.rejected, paid=False)


class TestWithoutAutoPayments:
    """Nothing recalculates the status, so the work keeps its old status unless going over
    the limit makes the receiver save it."""

    def test_new_work_stays_incomplete(self, submit_suspended_visit):
        """Case 4: A suspended worker's first visit to an entity.

        The receiver marks the work rejected but never saves it, and nothing recalculates
        the status, so the work stays incomplete.
        """
        visit = submit_suspended_visit(auto_approve_payments=False, work_approved=False)
        _assert_work(visit=visit, status=CompletedWorkStatus.incomplete, paid=False)

    def test_over_limit_new_work_is_rejected(self, submit_suspended_visit):
        """Case 5: A suspended worker's first visit to an entity, after the worker has reached the daily limit
        on other entities.

        The over-limit check marks the work to be saved, so the receiver saves the rejected
        status it set for the suspension.
        """
        visit = submit_suspended_visit(auto_approve_payments=False, work_approved=False, over_limit=True)
        _assert_work(visit=visit, status=CompletedWorkStatus.rejected, paid=False)

    def test_approved_work_stays_approved_and_paid(self, submit_suspended_visit):
        """Case 6: A suspended worker visits an entity they already have approved completed work for.

        Nothing recalculates the status, so the work stays approved and keeps its pay.
        """
        visit = submit_suspended_visit(auto_approve_payments=False, work_approved=True)
        _assert_work(visit=visit, status=CompletedWorkStatus.approved, paid=True)

    def test_over_limit_approved_work_stays_approved_and_paid(self, submit_suspended_visit):
        """Case 7: A suspended worker visits an entity they already have approved completed work for,
        and the visit goes over the daily limit.

        The over-limit check leaves approved work alone, so nothing marks the work to be saved
        and it stays approved and keeps its pay.
        """
        visit = submit_suspended_visit(auto_approve_payments=False, work_approved=True, over_limit=True)
        _assert_work(visit=visit, status=CompletedWorkStatus.approved, paid=True)


@pytest.fixture
def submit_suspended_visit(user_with_connectid_link, api_client, opportunity):
    return partial(
        _submit_suspended_worker_visit, opportunity=opportunity, user=user_with_connectid_link, api_client=api_client
    )


def _assert_work(visit, status, paid):
    """Check the suspended worker's visit is rejected, and its completed work's status and pay."""
    assert visit.status == VisitValidationStatus.rejected
    work = visit.completed_work
    assert work.status == status
    assert work.opportunity_access.payment_accrued == (work.payment_unit.amount if paid else 0)


def _submit_suspended_worker_visit(
    opportunity, user, api_client, auto_approve_payments, work_approved, over_limit=False
):
    """Submit a visit from a suspended worker and return that visit.

    With work_approved, the worker first submits a visit to the entity while active, and the
    worker's completed work for it is approved by hand, as the completed work status import
    does. With over_limit, the daily limit is 1 and the worker uses it up while active (on the
    approved visit, or else on a visit to another entity), so the suspended worker's visit
    goes over it.
    """
    form_json = _create_opp_and_form_json(opportunity, user=user, daily_max_per_user=1 if over_limit else 10)
    opportunity.auto_approve_visits = True
    opportunity.auto_approve_payments = auto_approve_payments
    opportunity.save()
    submit = partial(
        make_request, api_client=api_client, user=user, oauth_application=opportunity.hq_server.oauth_application
    )
    if work_approved:
        submit(form_json=form_json)
        CompletedWork.objects.filter(opportunity_access__user=user).update(status=CompletedWorkStatus.approved)
        update_payment_accrued(opportunity, users=[user])
    elif over_limit:
        submit(form_json=_resubmission(form_json=form_json, new_entity=True))

    OpportunityAccess.objects.filter(user=user).update(suspended=True)
    submit(form_json=_resubmission(form_json=form_json, new_entity=False))
    return UserVisit.objects.filter(user=user).latest("id")


def _resubmission(form_json, new_entity):
    """Copy a form with a new form ID, and a new entity ID when new_entity is set."""
    copy = deepcopy(form_json)
    copy["id"] = copy["metadata"]["instanceID"] = str(uuid4())
    if new_entity:
        copy["form"]["deliver"]["entity_id"] = str(uuid4())
    return copy
