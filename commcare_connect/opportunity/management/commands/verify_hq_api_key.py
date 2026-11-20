import httpx
from django.core.management.base import BaseCommand, CommandError

from commcare_connect.opportunity.models import Opportunity
from commcare_connect.utils.commcarehq_api import CommCareHQAPIException, get_app_structure, get_domains_for_user

HQ_ERROR_PREVIEW_LENGTH = 200


class Command(BaseCommand):
    help = "Check that an opportunity's CommCare HQ API key is accepted by HQ and can read its learn and deliver apps."

    def add_arguments(self, parser):
        parser.add_argument("opportunity_id", type=int, help="Primary key of the opportunity.")

    def handle(self, *args, opportunity_id, **options):
        opportunity = _get_opportunity(opportunity_id)
        api_key = opportunity.api_key
        self.stdout.write(
            f"Opportunity {opportunity.id} ({opportunity.name}): key ...{api_key.api_key[-4:]} "
            f"owned by {api_key.user.email} on {api_key.hq_server.url}, added {api_key.date_created:%Y-%m-%d}"
        )

        failures = 0
        for label, error in _run_checks(opportunity):
            if error is None:
                self.stdout.write(self.style.SUCCESS(f"OK    {label}"))
            else:
                failures += 1
                self.stdout.write(self.style.ERROR(f"FAIL  {label}: {error[:HQ_ERROR_PREVIEW_LENGTH]}"))

        if failures:
            raise CommandError(f"{failures} check(s) failed for opportunity {opportunity.id}'s API key.")
        self.stdout.write(self.style.SUCCESS("The API key works for this opportunity."))


def _get_opportunity(opportunity_id):
    opportunity = (
        Opportunity.objects.select_related("api_key__hq_server", "api_key__user", "learn_app", "deliver_app")
        .filter(id=opportunity_id)
        .first()
    )
    if opportunity is None:
        raise CommandError(f"Opportunity {opportunity_id} does not exist.")
    if opportunity.api_key is None:
        raise CommandError(f"Opportunity {opportunity_id} has no HQ API key.")
    if opportunity.api_key.hq_server is None:
        raise CommandError(f"Opportunity {opportunity_id}'s API key has no HQ server.")
    return opportunity


def _run_checks(opportunity):
    """Yield (label, error) for each check, with error None when the check passes."""
    api_key = opportunity.api_key
    yield "HQ accepts the key", _call_hq(get_domains_for_user, api_key)
    for app_type, app in (("learn", opportunity.learn_app), ("deliver", opportunity.deliver_app)):
        if app is not None:
            label = f"key can read the {app_type} app {app.name!r} in domain {app.cc_domain!r}"
            yield label, _call_hq(get_app_structure, api_key, app)


def _call_hq(hq_function, *args):
    try:
        hq_function(*args)
    except (CommCareHQAPIException, httpx.RequestError) as error:
        return str(error) or error.__class__.__name__
    return None
