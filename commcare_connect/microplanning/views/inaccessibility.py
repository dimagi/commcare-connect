import json
import logging
from functools import partial

import pghistory
from django.db import transaction
from django.db.models import TextChoices
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_POST
from waffle.decorators import waffle_flag

from commcare_connect.flags.flag_names import MICROPLANNING
from commcare_connect.microplanning.models import (
    InaccessibilityRequestStatus,
    WorkArea,
    WorkAreaInaccessibilityRequest,
    WorkAreaStatus,
)
from commcare_connect.microplanning.services import create_or_update_case_by_work_area
from commcare_connect.opportunity.models import BlobMeta
from commcare_connect.opportunity.tasks import send_push_notification_task
from commcare_connect.organization.decorators import (
    opp_admin_access_required,
    opportunity_required,
)
from commcare_connect.utils.commcarehq_api import CommCareHQAPIException

logger = logging.getLogger(__name__)


@require_GET
@opp_admin_access_required
@opportunity_required
@waffle_flag(MICROPLANNING)
def review_inaccessibility_request(request, org_slug, opp_id, work_area_id):
    work_area = get_object_or_404(
        WorkArea,
        id=work_area_id,
        opportunity=request.opportunity,
        status=WorkAreaStatus.REQUEST_FOR_INACCESSIBLE,
    )
    inacc_request = get_object_or_404(
        WorkAreaInaccessibilityRequest.objects.select_related("opportunity_access__user"),
        work_area=work_area,
        status=InaccessibilityRequestStatus.PENDING,
    )
    try:
        photo = BlobMeta.objects.get(parent_id=inacc_request.xform_id)
    except BlobMeta.DoesNotExist:
        photo = None
    return render(
        request,
        "microplanning/review_inaccessibility_panel.html",
        context={
            "work_area": work_area,
            "inaccessibility_request": inacc_request,
            "photo": photo,
        },
    )


class InaccessibilityReviewAction(TextChoices):
    APPROVE = "approve", "Approve"
    DENY = "deny", "Deny"


_ACTION_TO_NEW_STATUS = {
    InaccessibilityReviewAction.APPROVE: WorkAreaStatus.INACCESSIBLE,
    InaccessibilityReviewAction.DENY: WorkAreaStatus.NOT_VISITED,
}


_ACTION_TO_REQUEST_STATUS = {
    InaccessibilityReviewAction.APPROVE: InaccessibilityRequestStatus.APPROVED,
    InaccessibilityReviewAction.DENY: InaccessibilityRequestStatus.DENIED,
}


@require_POST
@opp_admin_access_required
@opportunity_required
@waffle_flag(MICROPLANNING)
def act_on_inaccessibility_request(request, org_slug, opp_id, work_area_id):
    try:
        action = InaccessibilityReviewAction(request.POST.get("action", ""))
    except ValueError:
        return HttpResponseBadRequest("Invalid action", content_type="text/plain")

    new_status = _ACTION_TO_NEW_STATUS[action]

    work_area = get_object_or_404(
        WorkArea.objects.select_for_update(),
        id=work_area_id,
        opportunity=request.opportunity,
    )
    inacc_request = (
        WorkAreaInaccessibilityRequest.objects.select_related("opportunity_access__user")
        .filter(work_area=work_area, status=InaccessibilityRequestStatus.PENDING)
        .first()
    )
    if inacc_request is None or work_area.status != WorkAreaStatus.REQUEST_FOR_INACCESSIBLE:
        # The work area is not in a state this action can act on, for any number of reasons, so
        # the panel is working from stale data either way. Its error box shows the body as text,
        # so this has to be a message rather than an error page.
        return HttpResponse(
            status=409,
            content=_("Invalid request. Please reload the page and try again."),
            content_type="text/plain",
        )

    work_area.status = new_status
    inacc_request.status = _ACTION_TO_REQUEST_STATUS[action]
    try:
        with transaction.atomic():
            with pghistory.context(username=request.user.username, user_email=request.user.email):
                work_area.save(update_fields=["status"])
            inacc_request.save(update_fields=["status"])
            if work_area.work_area_group and action == InaccessibilityReviewAction.APPROVE:
                work_area.work_area_group.update_centroid()
            if work_area.opportunity_access_id:
                create_or_update_case_by_work_area(work_area)

    except CommCareHQAPIException as e:
        logger.info(f"Failed to sync work area {work_area.id} to HQ after review action. Error: {e}")
        return HttpResponse(
            status=500,
            content=_("Failed to sync work area status. Please try again."),
            content_type="text/plain",
        )

    if action == InaccessibilityReviewAction.DENY:
        transaction.on_commit(
            partial(
                send_push_notification_task.delay,
                [inacc_request.opportunity_access.user_id],
                "Inaccessibility Request Denied",
                "Your request to mark a work area inaccessible has been declined.",
            )
        )

    response = HttpResponse(status=204)
    response["HX-Trigger"] = json.dumps({"inaccessibilityReviewed": {"id": work_area.id, "status": new_status}})
    return response
