"""Opportunities in each setup state: a draft never finalized, an active one, one that has ended, and one with
several payment units and verification rules."""

import datetime
from decimal import Decimal

from commcare_connect.simulation.constants import DELIVER_UNIT_SLUGS
from commcare_connect.simulation.steps import PaymentUnitSpec

from . import _common

TITLE = "Opp Setup"
ORG_NAMES = _common.org_names(TITLE)

# Started soon after the network manager joined the program, and ended last week.
ENDED_DAYS_AGO = 40
ENDED_RUN_DAYS = 33
MULTI_UNIT_PAYMENT_UNITS = [
    PaymentUnitSpec(name="Household visit", amount=Decimal("5"), deliver_units=[DELIVER_UNIT_SLUGS[0]]),
    PaymentUnitSpec(
        name="Child screening",
        amount=Decimal("8"),
        org_amount=Decimal("2"),
        max_total=30,
        max_daily=8,
        deliver_units=[DELIVER_UNIT_SLUGS[1]],
    ),
]


def build(sim, *, small):
    program, nm_org = _common.program(sim, TITLE)
    ended_start = _common.days_ago(ENDED_DAYS_AGO).date()
    _common.opportunity(
        sim,
        program,
        nm_org,
        "Ended Opportunity",
        days=ENDED_DAYS_AGO,
        end_date=ended_start + datetime.timedelta(days=ENDED_RUN_DAYS),
    )
    _common.opportunity(sim, program, nm_org, "Active Opportunity")
    _common.opportunity(
        sim,
        program,
        nm_org,
        "Multi-unit Opportunity",
        payment_units=MULTI_UNIT_PAYMENT_UNITS,
        verification_flags={"duplicate": True, "gps": True, "location": 50},
        deliver_unit_flags={slug: {"duration": 3} for slug in DELIVER_UNIT_SLUGS},
    )
    # Set up last week and never finalized, so it has no budget or worker limit yet.
    _common.opportunity(sim, program, nm_org, "Draft Opportunity", days=7, finalize=False)
