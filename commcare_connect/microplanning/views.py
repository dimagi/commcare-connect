import waffle
from django.conf import settings
from django.http import Http404
from django.shortcuts import render
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_GET

from commcare_connect.flags.switch_names import MICROPLANNING
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
