import datetime
import json
import logging

from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_GET

from commcare_connect.commcarehq.const import (
    APP_DOMAIN_PLACEHOLDER_CHOICE,
    APP_PLACEHOLDER_CHOICE,
    DOMAIN_PLACEHOLDER_CHOICE,
)
from commcare_connect.opportunity.models import HQApiKey, Opportunity
from commcare_connect.utils.commcarehq_api import (
    CommCareHQAccessDenied,
    CommCareHQAPIException,
    get_applications_for_user_by_domain,
    get_domains_for_user,
)

logger = logging.getLogger(__name__)

API_KEY_NOT_FOUND_CHOICE = ("", _("API key not found. Select another API key."))


# used for loading domain dropdown
@require_GET
@login_required
def get_domains(request):
    hq_server = request.GET.get("hq_server")
    api_key_id = request.GET.get("api_key")
    if not hq_server or not api_key_id:
        return _render_options([DOMAIN_PLACEHOLDER_CHOICE])

    return _render_options(domain_options(_get_api_key(request, hq_server, api_key_id)))


# used for loading learn_app and deliver_app dropdowns
@require_GET
@login_required
def get_application(request):
    hq_server = request.GET.get("hq_server")
    api_key_id = request.GET.get("api_key")
    domain = request.GET.get("learn_app_domain") or request.GET.get("deliver_app_domain")
    if not hq_server or not api_key_id or not domain:
        return _render_options([APP_DOMAIN_PLACEHOLDER_CHOICE])

    return _render_options(application_options(_get_api_key(request, hq_server, api_key_id), domain))


def domain_options(api_key):
    """Return the `(value, label)` options for the domain select, or a single message option when there are none."""
    if api_key is None:
        return [API_KEY_NOT_FOUND_CHOICE]
    try:
        domains = get_domains_for_user(api_key)
    except CommCareHQAPIException as e:
        return [
            _hq_error_option(
                e,
                denied_message=_(
                    "CommCare HQ refused this API key. Check that it is still active, or select another key."
                ),
                failed_message=_("Couldn't load domains from CommCare HQ. Try again."),
            )
        ]
    if not domains:
        return [("", _("No project spaces found for this API key."))]
    return [("", _("Select a Domain.")), *((domain, domain) for domain in domains)]


def application_options(api_key, domain):
    """Return the `(value, label)` options for an app select, leaving out apps that active opportunities already
    use, or a single message option when there are none to pick."""
    if api_key is None:
        return [API_KEY_NOT_FOUND_CHOICE]
    try:
        applications = get_applications_for_user_by_domain(api_key, domain)
    except CommCareHQAPIException as e:
        return [
            _hq_error_option(
                e,
                # The same key has just listed this domain, so a refusal here is about access to the domain's apps.
                denied_message=_(
                    "CommCare HQ refused access to this project space's apps. Check that the API key's user can "
                    "see its applications, or select another domain."
                ),
                failed_message=_("Couldn't load apps from CommCare HQ. Try again."),
            )
        ]
    if not applications:
        return [("", _("No apps found in this project space."))]

    used_app_ids = _app_ids_used_by_active_opportunities(domain)
    available = [(json.dumps(app), app["name"]) for app in applications if app["id"] not in used_app_ids]
    if not available:
        return [("", _("All apps in this project space are already used by active opportunities."))]
    return [APP_PLACEHOLDER_CHOICE, *available]


def _get_api_key(request, hq_server, api_key_id):
    """Return the user's API key, or None when the ID isn't one of theirs (or isn't a number)."""
    try:
        return (
            HQApiKey.objects.filter(id=api_key_id, hq_server=hq_server, user=request.user)
            .select_related("hq_server", "user")
            .first()
        )
    except (TypeError, ValueError):
        return None


def _app_ids_used_by_active_opportunities(domain):
    active_opp_apps = Opportunity.objects.filter(
        Q(learn_app__cc_domain=domain) | Q(deliver_app__cc_domain=domain),
        active=True,
        end_date__gte=datetime.date.today(),
    ).values_list("learn_app__cc_domain", "learn_app__cc_app_id", "deliver_app__cc_domain", "deliver_app__cc_app_id")
    used_app_ids = set()
    for learn_domain, learn_app_id, deliver_domain, deliver_app_id in active_opp_apps:
        if learn_domain == domain:
            used_app_ids.add(learn_app_id)
        if deliver_domain == domain:
            used_app_ids.add(deliver_app_id)
    return used_app_ids


def _hq_error_option(error, *, denied_message, failed_message):
    # A refused key is the user's to fix, so it's only a warning; anything else means HQ is failing us.
    if isinstance(error, CommCareHQAccessDenied):
        logger.warning("CommCare HQ refused a setup select request: %s", error)
        return ("", denied_message)
    logger.error("CommCare HQ request for a setup select failed: %s", error)
    return ("", failed_message)


def _render_options(options):
    """Render `(value, label)` pairs as `<option>` tags.

    Always answers 200, because htmx doesn't swap in an error response and the select would keep its old text."""
    return HttpResponse(render_to_string("commcarehq/select_options.html", {"options": options}))
