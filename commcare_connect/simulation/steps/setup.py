import datetime
import json
import uuid

from django.contrib.auth.models import Permission
from django.urls import resolve, reverse
from oauth2_provider.models import get_application_model
from waffle import switch_is_active

from commcare_connect.commcarehq.models import HQServer
from commcare_connect.flags.switch_names import ENABLE_PROGRAM_ACCESS_REDESIGN
from commcare_connect.opportunity.models import DeliverUnit, DeliverUnitFlagRules, DeliveryType, HQApiKey, Opportunity
from commcare_connect.organization.forms import OrganizationChangeForm
from commcare_connect.organization.models import Organization
from commcare_connect.program.models import Program, ProgramApplication
from commcare_connect.simulation.constants import SIM_DELIVER_APP_PREFIX, SIM_DOMAIN, SIM_LEARN_APP_PREFIX
from commcare_connect.users.models import User
from commcare_connect.utils.permission_const import ORG_MANAGEMENT_SETTINGS_ACCESS

from .types import PaymentUnitSpec

SIM_HQ_URL = "https://hq.simulation.local"
SIM_STAFF_USERNAME = "simulation-org-settings"
LEARN_PASSING_SCORE = 70
VERIFICATION_SAVED = "Verification rules saved successfully."
# The verification page drops these fields when the opportunity verifies visits automatically.
AUTO_VERIFY_HIDDEN_FLAGS = {"duplicate", "gps", "location"}
AUTO_VERIFY_HIDDEN_RULES = {"check_attachments"}


class SetupSteps:
    """Organizations, programs and opportunities, created by submitting the setup pages as the acting admin."""

    def org(self, name, *, admin, program_manager=False):
        """Create an organization with ``admin`` as its admin member. Returns the ``Organization``.

        Submits the "Create organization" page as ``admin``. Only Dimagi staff can enable program management, on
        Organization Home, so ``program_manager`` submits that page as the simulation's staff user.
        """
        with self.step():
            response = self.browser(admin).post(reverse("organization_create"), {"name": name})
            organization = Organization.objects.get(slug=resolve(response.url).kwargs["org_slug"])
            if program_manager:
                _enable_program_manager(self.browser(_org_settings_staff()), organization)
            self.log(f"Created organization {organization.slug}{' (program manager)' if program_manager else ''}")
        return organization

    def program(
        self,
        pm_org,
        *,
        name,
        budget=100_000,
        start_date=None,
        end_date=None,
        currency="USD",
        country="USA",
    ):
        """Create a program owned by ``pm_org`` on its "Add program" form, as ``pm_admin``. Returns the ``Program``.

        Dates default to a window from a month ago to six months ahead, which leaves room for opportunities that
        start today.
        """
        today = datetime.date.today()
        data = {
            "name": name,
            "description": f"{name} (simulated)",
            "delivery_type": _delivery_type().pk,
            "budget": budget,
            "currency": currency,
            "country": country,
            "start_date": today - datetime.timedelta(days=30) if start_date is None else start_date,
            "end_date": today + datetime.timedelta(days=180) if end_date is None else end_date,
        }
        with self.step():
            self.browser(self.pm_admin).post(reverse("program:init", args=(pm_org.slug,)), data, htmx=True)
            program = Program.objects.filter(organization=pm_org, name=name).latest("pk")
            self.log(f"Created program {program.name} for {pm_org.slug}")
        self.link(f"Programs of {pm_org.name}", reverse("program:home", kwargs={"org_slug": pm_org.slug}))
        return program

    def join_program(self, program, nm_org):
        """Invite ``nm_org`` to ``program`` (as PM), apply (as NM) and accept (as PM), on the program home pages.

        Returns the accepted ``ProgramApplication``.
        """
        pm_slug = program.organization.slug
        with self.step():
            self.browser(self.pm_admin).post(
                reverse("program:invite_organization", args=(pm_slug, program.program_id)),
                {"organization": nm_org.slug},
                succeeded=_invited(program, nm_org),
            )
            application = ProgramApplication.objects.get(program=program, organization=nm_org)
            self.browser(self.nm_admin).post(
                reverse(
                    "program:apply_or_decline_application",
                    args=(nm_org.slug, program.program_id, application.program_application_id, "apply"),
                ),
                htmx=True,
            )
            self.browser(self.pm_admin).post(
                reverse("program:manage_application", args=(pm_slug, application.id, "accept")), htmx=True
            )
            application.refresh_from_db()
            self.log(f"{nm_org.slug} joined program {program.name}")
        return application

    def opportunity(
        self,
        program,
        nm_org,
        *,
        name,
        payment_units=None,
        start_date=None,
        end_date=None,
        total_budget=None,
        max_users=10,
        finalize=True,
        verification_flags=None,
        deliver_unit_flags=None,
    ):
        """Create a managed opportunity for ``nm_org`` under ``program`` through the setup wizard, as ``pm_admin``.

        Submits the program's "Create opportunity" page (which syncs the learn modules and deliver units), the
        "Add payment unit" form once per ``PaymentUnitSpec`` in ``payment_units`` (one default unit when omitted)
        and, when ``finalize`` is true, the finalize page. ``verification_flags`` is a dict of
        ``OpportunityVerificationFlagsConfigForm`` fields, and ``deliver_unit_flags`` maps a deliver-unit slug to
        its ``DeliverUnitFlagsForm`` fields (``duration``, ``check_attachments``); either submits the verification
        rules page. Each opportunity gets its own simulated app ids. Returns the ``Opportunity``.

        Finalizing defaults to starting today and running 60 days (within the program), with the budget for
        ``max_users`` workers.
        """
        browser = self.browser(self.pm_admin)
        pm_slug = program.organization.slug
        with self.step():
            opportunity = _init_opportunity(browser, program, nm_org, name)
            self.log(f"Created opportunity {opportunity.name} for {nm_org.slug}")
        opp_args = (pm_slug, opportunity.opportunity_id)
        with self.step():
            for spec in [PaymentUnitSpec()] if payment_units is None else payment_units:
                browser.post(
                    reverse("opportunity:add_payment_units", args=opp_args), _payment_unit_data(opportunity, spec)
                )
        if finalize:
            with self.step():
                data = _finalize_data(opportunity, start_date, end_date, total_budget, max_users)
                # With no payment units the page redirects back to add them instead of saving.
                browser.post(
                    reverse("opportunity:finalize", args=opp_args), data, succeeded=_redirects_to("opportunity:detail")
                )
                opportunity.refresh_from_db()
                self.log(f"Finalized {opportunity.name}: budget {opportunity.total_budget}")
        if verification_flags or deliver_unit_flags:
            with self.step():
                browser.post(
                    reverse("opportunity:verification_flags_config", args=opp_args),
                    _verification_data(opportunity, verification_flags or {}, deliver_unit_flags or {}),
                    succeeded=_verification_saved,
                )
        opportunity.refresh_from_db()
        self.link(
            f"Opportunity {opportunity.name}",
            reverse("opportunity:detail", args=(nm_org.slug, opportunity.opportunity_id)),
        )
        return opportunity


def _enable_program_manager(browser, organization):
    """Submit Organization Home's profile form unchanged except for "Enable Program Manager"."""
    current = OrganizationChangeForm(instance=organization, user=browser.user)
    data = {name: _checkbox(current[name].value()) for name in current.fields}
    data = {name: value for name, value in data.items() if value is not None and value is not False}
    data["program_manager"] = "on"
    browser.post(reverse("organization:home", args=(organization.slug,)), data)


def _org_settings_staff():
    """The Dimagi staff member who manages organization settings: a configuration user holding only
    ``ORG_MANAGEMENT_SETTINGS_ACCESS``, with no memberships and no usable password, so get-or-created."""
    staff, created = User.objects.get_or_create(
        username=SIM_STAFF_USERNAME,
        defaults={"email": f"{SIM_STAFF_USERNAME}@simulation.local", "name": "Simulation org settings"},
    )
    if created:
        staff.set_unusable_password()
        staff.save()
    app_label, codename = ORG_MANAGEMENT_SETTINGS_ACCESS.split(".")
    staff.user_permissions.add(Permission.objects.get(content_type__app_label=app_label, codename=codename))
    return staff


def _delivery_type():
    """Delivery types are managed in Django admin only, so the simulation's is get-or-created."""
    delivery_type, _created = DeliveryType.objects.get_or_create(
        slug="simulation", defaults={"name": "Simulation", "description": "Programs built by the simulation"}
    )
    return delivery_type


def _invited(program, organization):
    """The invite view redirects whether or not it invited, so success is the invitation existing."""

    def succeeded(response):
        return (
            300 <= response.status_code < 400
            and ProgramApplication.objects.filter(program=program, organization=organization).exists()
        )

    return succeeded


def _init_opportunity(browser, program, nm_org, name):
    """Submit the program's "Create opportunity" page; its view also syncs the apps and emails the NM org."""
    pm_slug = program.organization.slug
    hq_server = _sim_hq_server()
    # Unique per opportunity: the form receiver resolves a submission to the one opportunity using its app.
    app_suffix = uuid.uuid4().hex[:12]
    data = {
        "name": name,
        "short_description": f"{name} (simulated)",
        "description": f"{name}, created by the simulation.",
        "organization": nm_org.pk,
        "hq_server": hq_server.pk,
        "api_key": _api_key(browser, pm_slug, hq_server).pk,
        "learn_app_domain": SIM_DOMAIN,
        "learn_app": json.dumps({"id": f"{SIM_LEARN_APP_PREFIX}{app_suffix}", "name": f"{name} Learn"}),
        "learn_app_description": f"Training for {name}",
        # process_assessments compares scores against it, so it must be set.
        "learn_app_passing_score": LEARN_PASSING_SCORE,
        "deliver_app_domain": SIM_DOMAIN,
        "deliver_app": json.dumps({"id": f"{SIM_DELIVER_APP_PREFIX}{app_suffix}", "name": f"{name} Deliver"}),
    }
    if switch_is_active(ENABLE_PROGRAM_ACCESS_REDESIGN):
        data["supervising_organization"] = program.organization.pk
    response = browser.post(reverse("program:opportunity_init", args=(pm_slug, program.program_id)), data)
    return Opportunity.objects.get(opportunity_id=resolve(response.url).kwargs["opp_id"])


def _sim_hq_server():
    """The HQ server the simulated apps live on. A configuration record with no UI, so get-or-created."""
    hq_server = HQServer.objects.filter(url=SIM_HQ_URL).first()
    if hq_server:
        return hq_server
    application_model = get_application_model()
    application = application_model.objects.create(
        name="Simulated HQ",
        client_type=application_model.CLIENT_CONFIDENTIAL,
        authorization_grant_type=application_model.GRANT_CLIENT_CREDENTIALS,
    )
    return HQServer.objects.create(name="Simulated HQ", url=SIM_HQ_URL, oauth_application=application)


def _api_key(browser, org_slug, hq_server):
    """The acting user's key for ``hq_server``, added through the opportunity page's "Add API key" form if missing."""
    existing = HQApiKey.objects.filter(user=browser.user, hq_server=hq_server).first()
    if existing:
        return existing
    key = f"sim-{uuid.uuid4().hex}"
    # The view answers with the form fragment either way: blank after a save, with errors otherwise.
    browser.post(
        reverse("opportunity:add_api_key", args=(org_slug,)),
        {"hq_server": hq_server.pk, "api_key": key},
        htmx=True,
        succeeded=lambda response: HQApiKey.objects.filter(api_key=key).exists(),
    )
    return HQApiKey.objects.get(api_key=key)


def _payment_unit_data(opportunity, spec):
    """The "Add payment unit" form as filled in for ``spec``."""
    deliver_units = _deliver_units_by_slug(opportunity, spec.deliver_units)
    data = {
        "name": spec.name,
        "description": f"{spec.name} (simulated)",
        "amount": spec.amount,
        "org_amount": spec.org_amount,
        "max_total": spec.max_total,
        "required_deliver_units": [deliver_unit.id for deliver_unit in deliver_units.values()],
    }
    if spec.max_daily is None:
        data["no_daily_limit"] = "on"
    else:
        data["max_daily"] = spec.max_daily
    return data


def _deliver_units_by_slug(opportunity, slugs):
    """The opportunity's deliver units named by ``slugs``; a slug that names none is a scenario mistake."""
    available = {du.slug: du for du in DeliverUnit.objects.filter(app=opportunity.deliver_app)}
    unknown = set(slugs) - set(available)
    if unknown:
        raise ValueError(f"No deliver unit {sorted(unknown)} in {opportunity.name}; it has {sorted(available)}.")
    return {slug: available[slug] for slug in slugs}


def _finalize_data(opportunity, start_date, end_date, total_budget, max_users):
    """The finalize page's fields; the page fills the total budget in from the number of workers."""
    today = datetime.date.today()
    if start_date is None:
        start_date = today
    if end_date is None:
        end_date = min(opportunity.program.end_date, start_date + datetime.timedelta(days=60))
    if total_budget is None:
        # Payment unit amounts are whole numbers, so this is whole too, as the page fills it in.
        total_budget = int(opportunity.budget_per_user() * max_users)
    return {"start_date": start_date, "end_date": end_date, "max_users": max_users, "total_budget": total_budget}


def _redirects_to(view_name):
    def succeeded(response):
        return 300 <= response.status_code < 400 and resolve(response.url).view_name == view_name

    return succeeded


def _verification_data(opportunity, verification_flags, deliver_unit_flags):
    """The "Verification Rules Configuration" page as it renders: one rule form per deliver unit without rules,
    in the order the page lists them, and one blank form JSON rule."""
    _deliver_units_by_slug(opportunity, deliver_unit_flags)
    auto_verify = opportunity.automatic_visit_verification
    if auto_verify:
        hidden = (AUTO_VERIFY_HIDDEN_FLAGS & set(verification_flags)) | {
            key for rules in deliver_unit_flags.values() for key in AUTO_VERIFY_HIDDEN_RULES & set(rules)
        }
        if hidden:
            raise ValueError(f"{opportunity.name} verifies visits automatically; its page has no {sorted(hidden)}.")
    data = {} if auto_verify else {"location": 0}
    data |= {key: _checkbox(value) for key, value in verification_flags.items() if value is not False}
    configured = DeliverUnitFlagRules.objects.filter(opportunity=opportunity)
    # The same queryset the view builds the rule forms' initial deliver units from. Their deliver unit field is
    # disabled, so the browser doesn't send it.
    deliver_units = list(opportunity.deliver_app.deliver_units.exclude(id__in=configured.values_list("deliver_unit")))
    for index, deliver_unit in enumerate(deliver_units):
        rules = {"duration": 0} | deliver_unit_flags.get(deliver_unit.slug, {})
        data |= {f"deliver_unit-{index}-{key}": _checkbox(value) for key, value in rules.items() if value is not False}
    data |= _management_form("deliver_unit", total=len(deliver_units), max_num=len(deliver_units))
    data |= _management_form("form_json", total=1, max_num=1000)
    return data


def _checkbox(value):
    """A ticked checkbox posts "on"; other inputs post their value."""
    return "on" if value is True else value


def _management_form(prefix, *, total, max_num):
    return {
        f"{prefix}-TOTAL_FORMS": total,
        f"{prefix}-INITIAL_FORMS": 0,
        f"{prefix}-MIN_NUM_FORMS": 0,
        f"{prefix}-MAX_NUM_FORMS": max_num,
    }


def _verification_saved(response):
    """The page answers both a saved and a refused submit with itself; only a save adds this message."""
    return VERIFICATION_SAVED in response.step_messages
