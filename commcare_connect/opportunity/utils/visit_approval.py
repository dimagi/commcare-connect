from django.db.models import Q, QuerySet
from django.utils.timezone import now

from commcare_connect.opportunity.models import Opportunity, UserVisit, VisitReviewStatus, VisitValidationStatus
from commcare_connect.opportunity.visit_import import update_payment_accrued


class MissingJustificationError(Exception):
    pass


def approvable_visits(opportunity: Opportunity, visit_ids) -> QuerySet[UserVisit]:
    """The opportunity's visits among visit_ids that are not approved yet, or that a PM disagreed with."""
    return (
        UserVisit.objects.filter(id__in=visit_ids, opportunity=opportunity)
        .filter(~Q(status=VisitValidationStatus.approved) | Q(review_status=VisitReviewStatus.disagree))
        .select_related("work_area", "work_area__opportunity_access")
    )


def approve_user_visits(opportunity: Opportunity, visits, justification: str | None = None) -> int:
    """Approve the visits for PM review, then recalculate the workers' accrual and the visits' work areas.

    Flagged visits get the justification, and MissingJustificationError is raised before anything is
    written if one of them needs it and none is given. A visit the PM disagreed with goes back to pending
    review. Returns the number of visits updated.
    """
    visits = list(visits)
    if not justification and any(visit.flagged for visit in visits):
        raise MissingJustificationError("Justification is mandatory for flagged visits.")

    approved_on = now()
    for visit in visits:
        _approve(visit, approved_on, justification)

    approved_count = UserVisit.objects.bulk_update(
        visits,
        [
            "status",
            "status_modified_date",
            "review_created_on",
            "review_status",
            "review_status_modified_date",
            "justification",
        ],
    )
    user_ids = list({visit.user_id for visit in visits})
    if user_ids:
        update_payment_accrued(opportunity=opportunity, users=user_ids, incremental=True)
    for work_area in {visit.work_area for visit in visits if visit.work_area}:
        work_area.update_status()
    return approved_count


def _approve(visit: UserVisit, approved_on, justification):
    visit.status = VisitValidationStatus.approved
    visit.review_created_on = approved_on
    if visit.review_status == VisitReviewStatus.disagree:
        visit.review_status = VisitReviewStatus.pending
        visit.review_status_modified_date = approved_on
    if visit.flagged:
        visit.justification = justification
