import datetime
import json

from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import HttpResponse
from django.utils.html import format_html

from commcare_connect.opportunity.models import HQApiKey, Opportunity
from commcare_connect.utils.commcarehq_api import get_applications_for_user_by_domain, get_domains_for_user


# used for loading domain dropdown
@login_required
def get_domains(request):
    hq_server = request.GET.get("hq_server")
    api_key_id = request.GET.get("api_key")
    if not hq_server or not api_key_id:
        return HttpResponse(format_html("<option value='{}'>{}</option>", None, "Select an API Key to load domains."))

    options = []
    api_key = HQApiKey.objects.get(id=api_key_id, hq_server=hq_server, user=request.user)
    domains = get_domains_for_user(api_key)
    options.append(format_html("<option value='{}'>{}</option>", None, "Select a Domain."))
    for domain in domains:
        options.append(format_html("<option value='{}'>{}</option>", domain, domain))
    return HttpResponse("\n".join(options))


# used for loading learn_app and deliver_app dropdowns
@login_required
def get_application(request):
    hq_server = request.GET.get("hq_server")
    api_key_id = request.GET.get("api_key")
    domain = request.GET.get("learn_app_domain") or request.GET.get("deliver_app_domain")
    if not hq_server or not api_key_id or not domain:
        return HttpResponse(
            format_html("<option value='{}'>{}</option>", None, "Select a Domain to load applications.")
        )
    api_key = HQApiKey.objects.get(id=api_key_id, hq_server=hq_server, user=request.user)
    applications = get_applications_for_user_by_domain(api_key, domain)
    active_opps = Opportunity.objects.filter(
        Q(learn_app__cc_domain=domain) | Q(deliver_app__cc_domain=domain),
        active=True,
        end_date__lt=datetime.date.today(),
    ).select_related("learn_app", "deliver_app")
    existing_apps = set()
    for opp in active_opps:
        if opp.learn_app.cc_domain == domain:
            existing_apps.add(opp.learn_app.cc_app_id)
        if opp.deliver_app.cc_domain == domain:
            existing_apps.add(opp.deliver_app.cc_app_id)
    options = []
    options.append(format_html("<option value='{}'>{}</option>", None, "Select an Application"))
    for app in applications:
        if app["id"] not in existing_apps:
            value = json.dumps(app)
            name = app["name"]
            options.append(format_html("<option value='{}'>{}</option>", value, name))
    return HttpResponse("\n".join(options))
