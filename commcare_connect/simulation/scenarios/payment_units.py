"""An opportunity paying for two kinds of work at different rates: visits delivered against each payment unit,
workers paid in full and in part, and an invoice whose line items split the work by unit."""

from commcare_connect.simulation.constants import DELIVER_UNIT_SLUGS

from . import _common
from .opp_setup import MULTI_UNIT_PAYMENT_UNITS

TITLE = "Payment Units"
ORG_NAMES = _common.org_names(TITLE)

# Delivered up to the evening of DELIVERED_DAYS_AGO, paid two days later and invoiced the day after, so the invoice
# (which ends the day before it is raised) covers every visit.
DELIVERED_DAYS_AGO = 5
PAID_DAYS_AGO = 3
INVOICED_DAYS_AGO = 2


def build(sim, *, small):
    opportunity, workers = _common.staffed_opportunity(
        sim, TITLE, 2 if small else 8, payment_units=MULTI_UNIT_PAYMENT_UNITS
    )
    # Approved on arrival, since the opportunity has no verification rules.
    household_visits, screenings = (2, 1) if small else (8, 5)
    with sim.at(_common.days_ago(DELIVERED_DAYS_AGO, hour=19)):
        for slug, per_worker in zip(DELIVER_UNIT_SLUGS, (household_visits, screenings)):
            sim.deliver(opportunity, workers, per_worker=per_worker, over_days=14, deliver_unit=slug)
    with sim.at(_common.days_ago(PAID_DAYS_AGO)):
        half = len(workers) // 2
        sim.pay(opportunity, workers[:half])
        sim.pay(opportunity, workers[half:], fraction=0.5)
    with sim.at(_common.days_ago(INVOICED_DAYS_AGO)):
        sim.invoice(opportunity)
    _common.link_page(sim, opportunity, "invoices", "opportunity:invoice_list")
