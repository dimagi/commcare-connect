from django.conf import settings
from django.shortcuts import render
from django.utils.timezone import localdate
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET

from commcare_connect.flags.decorators import require_flag_for_opp
from commcare_connect.flags.flag_names import MICROPLANNING
from commcare_connect.organization.decorators import opportunity_required, org_admin_required


@require_GET
@org_admin_required
@opportunity_required
@require_flag_for_opp(MICROPLANNING)
def microplanning_home(request, *args, **kwargs):
    opportunity = request.opportunity
    return render(
        request,
        template_name="microplanning/home.html",
        context={
            "mapbox_api_key": settings.MAPBOX_TOKEN,
            "opportunity": opportunity,
            "metrics": get_metrics_for_microplanning(opportunity),
        },
    )


def get_metrics_for_microplanning(opportunity):
    return [
        {
            "name": _("Days Remaining"),
            "value": max((opportunity.end_date - localdate()).days, 0) if opportunity.end_date else "--",
        },
    ]
