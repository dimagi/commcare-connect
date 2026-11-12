import uuid
from http import HTTPStatus

from celery.result import AsyncResult
from crispy_forms.utils import render_crispy_form
from django.contrib import messages
from django.core.cache import cache
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST
from waffle.decorators import waffle_flag

from commcare_connect.flags.flag_names import MICROPLANNING
from commcare_connect.microplanning.forms import ClusterWorkAreasForm
from commcare_connect.microplanning.models import (
    WorkArea,
    WorkAreaGroup,
)
from commcare_connect.microplanning.tasks import (
    cluster_work_areas_task,
    get_cluster_area_cache_lock_key,
)
from commcare_connect.organization.decorators import (
    opp_admin_access_required,
    opportunity_required,
)
from commcare_connect.utils.celery import CELERY_TASK_FAILURE, CELERY_TASK_SUCCESS


@opp_admin_access_required
@opportunity_required
@require_POST
@waffle_flag(MICROPLANNING)
def cluster_work_areas(request, org_slug, opp_id):
    redirect_url = reverse(
        "microplanning:microplanning_home",
        kwargs={"org_slug": org_slug, "opp_id": opp_id},
    )

    rerun = request.GET.get("rerun") is not None
    if rerun:
        if WorkArea.objects.filter(opportunity_id=request.opportunity.id, opportunity_access__isnull=False).exists():
            messages.error(
                request, _("Clustering cannot be re-run because Work Areas have already been assigned to FLWs.")
            )
            return HttpResponse(headers={"HX-Redirect": redirect_url})
        WorkAreaGroup.objects.filter(opportunity_id=request.opportunity.id).delete()

    if not WorkArea.objects.filter(opportunity_id=request.opportunity.id).exists():
        messages.error(request, _("Please upload Work Areas for this opportunity."))
        return HttpResponse(headers={"HX-Redirect": redirect_url})

    if WorkAreaGroup.objects.filter(opportunity_id=request.opportunity.id).exists():
        messages.error(request, _("Work Area Groups already exist for this opportunity."))
        return HttpResponse(headers={"HX-Redirect": redirect_url})

    form = ClusterWorkAreasForm(request.POST)
    if not form.is_valid():
        # Retarget the swap onto the field container so the error renders in
        # place, keeping the Create Groups button and the modal untouched.
        response = HttpResponse(render_crispy_form(form))
        response.headers["HX-Retarget"] = "#building-count-field"
        response.headers["HX-Reswap"] = "outerHTML"
        return response

    lock_key = get_cluster_area_cache_lock_key(request.opportunity.id)
    if cache.lock(lock_key).locked():
        messages.error(request, _("Work Area Clustering is already in progress for this opportunity."))
        return HttpResponse(headers={"HX-Redirect": redirect_url})

    task = cluster_work_areas_task.delay(request.opportunity.id, form.cleaned_data["building_count"])
    redirect_url += f"?clustering_task_id={task.id}"
    response = render(
        request,
        "microplanning/cluster_work_area_modal_status.html",
        context={"clustering_task_id": task.id},
    )
    response.headers["HX-Push-Url"] = redirect_url
    return response


@opp_admin_access_required
@opportunity_required
def clustering_status(request, org_slug, opp_id):
    task_id = request.GET.get("clustering_task_id", None)
    redirect_url = reverse("microplanning:microplanning_home", args=(org_slug, opp_id))

    if task_id:
        try:
            uuid.UUID(task_id)
        except (ValueError, TypeError):
            return redirect("microplanning:microplanning_home", org_slug=org_slug, opp_id=opp_id)

        task = AsyncResult(task_id)
        status = task.state
        message = None
        icon = None
        refresh_page = False

        if status == CELERY_TASK_SUCCESS:
            message = _("Work Area Clustering was successful. You may close this window.")
            icon = "fa-solid fa-circle-check text-green-600"
            refresh_page = True
            messages.success(request, "Work Area Clustering was successful.")
        elif status == CELERY_TASK_FAILURE:
            message = _("There was an error. Please try again.")
            icon = "fa-solid fa-circle-exclamation text-red-600"
        else:
            # htmx does not swap content when status 204 is returned.
            # This keeps the progress bar intact, once any of the above
            # status are triggered, the progress bar is replaced with a
            # non-refreshing div to show final status.
            return HttpResponse(status=HTTPStatus.NO_CONTENT)

        response = render(
            request,
            "microplanning/cluster_work_area_final_status.html",
            context={"icon": icon, "message": message},
        )
        if refresh_page:
            response.headers["HX-Redirect"] = redirect_url
        return response

    return HttpResponse(headers={"HX-Redirect": redirect_url})


@require_POST
@opp_admin_access_required
@opportunity_required
@waffle_flag(MICROPLANNING)
def clear_work_area_groups(request, org_slug, opp_id):
    redirect_url = reverse(
        "microplanning:microplanning_home",
        kwargs={"org_slug": org_slug, "opp_id": opp_id},
    )
    if WorkArea.objects.filter(opportunity_id=request.opportunity.id, opportunity_access__isnull=False).exists():
        messages.error(request, _("Work Areas already assigned to users. Work Area Groups cannot be cleared."))
        return HttpResponse(headers={"HX-Redirect": redirect_url})

    WorkAreaGroup.objects.filter(opportunity_id=request.opportunity.id).delete()
    messages.success(request, _("Work Area Groups have been cleared for this opportunity."))
    return HttpResponse(headers={"HX-Redirect": redirect_url})
