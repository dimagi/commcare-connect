"""RFPs and EOIs in every state the marketplace and the program manager's list tell apart: public and open (across
countries, delivery types and deadlines), private with an invited organization, a draft, a public one scheduled to go
live that the marketplace doesn't show yet, one past its deadline that the hourly task hasn't closed yet, and one it
has closed."""

from django.urls import reverse

from commcare_connect.simulation.steps import SolicitationSpec
from commcare_connect.simulation.steps.solicitations import require_solicitations_switch
from commcare_connect.solicitation.models import Solicitation

from . import _common

TITLE = "Solicitations"
ORG_NAMES = _common.org_names(TITLE)

RFP, EOI = Solicitation.Type.RFP, Solicitation.Type.EOI
PUBLISHED_DAYS_AGO = 10
# Closed by the hourly task: published long ago, with a deadline that passed weeks ago.
CLOSED_PUBLISHED_DAYS_AGO = 40
CLOSED_DEADLINE_DAYS_AGO = 20
# Past its deadline, but built after the task ran, so still active until it next runs.
EXPIRED_PUBLISHED_DAYS_AGO = 30
EXPIRED_DEADLINE_DAYS_AGO = 2

# (title, type, country, delivery type, days until the deadline). The first two are the small build: both types,
# two countries and two delivery types. The rest spread the deadlines over the next six months and fill more than
# one page of the marketplace.
PUBLIC = [
    ("Malaria case management in Kisumu", RFP, "KEN", "Malaria", 12),
    ("Child nutrition screening partners", EOI, "UGA", "Nutrition", 75),
    ("Bed net distribution, Lake Zone", RFP, "UGA", "Malaria", 5),
    ("Measles catch-up campaign", RFP, "NGA", "Immunization", 20),
    ("Community nutrition counselling", EOI, "KEN", "Nutrition", 28),
    ("Seasonal malaria chemoprevention", RFP, "NGA", "Malaria", 40),
    ("Routine immunization defaulter tracing", EOI, "MWI", "Immunization", 55),
    ("Growth monitoring in Lilongwe", RFP, "MWI", "Nutrition", 85),
    ("Malaria rapid testing at home", EOI, "IND", "Malaria", 100),
    ("HPV vaccination outreach", RFP, "KEN", "Immunization", 120),
    ("Vitamin A supplementation rounds", RFP, "IND", "Nutrition", 140),
    ("Polio campaign monitoring", EOI, "NGA", "Immunization", 160),
    ("Malaria in pregnancy follow-up", RFP, "MWI", "Malaria", 170),
    ("School feeding programme partners", EOI, "UGA", "Nutrition", 175),
]


def build(sim, *, small):
    require_solicitations_switch()
    program, nm_org = _common.program(sim, TITLE)
    pm_org = program.organization
    public = PUBLIC[:2] if small else PUBLIC
    # Worked out from the real now, before sim.at moves the clock.
    closed_deadline = _common.days_ago(CLOSED_DEADLINE_DAYS_AGO)
    expired_deadline = _common.days_ago(EXPIRED_DEADLINE_DAYS_AGO)
    open_deadline = _common.days_ahead(30)
    go_live = _common.days_ahead(7)
    public_deadlines = [_common.days_ahead(days) for *_, days in public]

    with sim.at(_common.days_ago(CLOSED_PUBLISHED_DAYS_AGO)):
        sim.solicitation(pm_org, SolicitationSpec(title="Closed: malaria mapping", deadline=closed_deadline))
    sim.close_expired_solicitations()
    with sim.at(_common.days_ago(EXPIRED_PUBLISHED_DAYS_AGO)):
        sim.solicitation(
            pm_org, SolicitationSpec(title="Expired: nutrition survey", type=EOI, deadline=expired_deadline)
        )
    with sim.at(_common.days_ago(PUBLISHED_DAYS_AGO)):
        for index, ((title, kind, country, delivery_type, _days), deadline) in enumerate(
            zip(public, public_deadlines, strict=True)
        ):
            spec = SolicitationSpec(
                title=title,
                deadline=deadline,
                type=kind,
                country=country,
                delivery_type=delivery_type,
                budget=(10_000 * (index + 1), 50_000 * (index + 1)),
                # One is linked to the program. Picking it fills in the program's delivery type, which the PM
                # changed here.
                program=program if index == 0 else None,
            )
            sim.solicitation(pm_org, spec)
        private = sim.solicitation(
            pm_org, SolicitationSpec(title="Private: immunization partners", public=False, deadline=open_deadline)
        )
        sim.invite_to_solicitation(private, [nm_org])
        sim.solicitation(
            pm_org, SolicitationSpec(title="Draft: maternal health", deadline=open_deadline), publish=False
        )
        sim.solicitation(
            pm_org,
            SolicitationSpec(title="Scheduled: CHW refresher training", deadline=open_deadline),
            go_live=go_live,
        )
    sim.link("Solicitations Marketplace", reverse("marketplace:list"))
