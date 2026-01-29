import waffle
from django.conf import settings
from django.http import Http404, JsonResponse
from django.shortcuts import render
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_GET

from commcare_connect.flags.switch_names import MICROPLANNING
from commcare_connect.microplanning.forms import AreaSelectionForm
from commcare_connect.microplanning.helper import get_wards_feature_collections
from commcare_connect.organization.decorators import org_viewer_required


@require_GET
@org_viewer_required
def microplanning_home(request, *args, **kwargs):
    if not waffle.switch_is_active(MICROPLANNING):
        raise Http404(_("Microplanning feature is not available"))

    return render(
        request,
        template_name="microplanning/home.html",
        context={
            "mapbox_api_key": settings.MAPBOX_TOKEN,
        },
    )


@require_GET
@org_viewer_required
def get_area_selection_form(request, *args, **kwargs):
    if not waffle.switch_is_active(MICROPLANNING):
        raise Http404(_("Microplanning feature is not available"))

    return render(
        request,
        template_name="microplanning/settings_panel/area_selection_card.html",
        context={
            "form": AreaSelectionForm(),
        },
    )


@require_GET
@org_viewer_required
def get_wards_features(request, *args, **kwargs):
    if not waffle.switch_is_active(MICROPLANNING):
        raise Http404(_("Microplanning feature is not available"))

    wards_ids = request.GET.get("wards", None)
    if not wards_ids:
        return JsonResponse(data=[])

    return JsonResponse(
        data=list(get_wards_feature_collections(wards_ids)),
    )
