"""Approved, agreed work paid to workers in full and in part, and invoiced in three rounds: one paid, one
approved and ready to pay, and one waiting for the program manager's review."""

from commcare_connect.opportunity.models import InvoiceStatus

from . import _common

TITLE = "Payments"
ORG_NAMES = _common.org_names(TITLE)

# Each round delivers over the days up to its day, after the previous round's payments, and is invoiced the next
# day, so the invoice (which ends the day before it is raised) covers it.
FIRST_ROUND_DAYS_AGO = 12
SECOND_ROUND_DAYS_AGO = 6
LAST_ROUND_DAYS_AGO = 3
# Late afternoon, so every visit of the round's last day is in by then.
ROUND_END_HOUR = 19


def build(sim, *, small):
    opportunity, workers = _common.staffed_opportunity(sim, TITLE, 2 if small else 10)

    paid = _round(sim, opportunity, workers, FIRST_ROUND_DAYS_AGO, per_worker=2 if small else 8, over_days=10)
    with sim.at(_common.days_ago(FIRST_ROUND_DAYS_AGO - 2)):
        sim.invoice_status(paid, InvoiceStatus.PENDING_PM_REVIEW)
    with sim.at(_common.days_ago(FIRST_ROUND_DAYS_AGO - 3)):
        sim.invoice_status(paid, InvoiceStatus.READY_TO_PAY)
    with sim.at(_common.days_ago(FIRST_ROUND_DAYS_AGO - 4)):
        sim.invoice_pay(paid)
        half = len(workers) // 2
        sim.pay(opportunity, workers[:half])
        sim.pay(opportunity, workers[half:], fraction=0.5)

    ready = _round(sim, opportunity, workers, SECOND_ROUND_DAYS_AGO, per_worker=1 if small else 4, over_days=2)
    with sim.at(_common.days_ago(SECOND_ROUND_DAYS_AGO - 1, hour=15)):
        sim.invoice_status(ready, InvoiceStatus.PENDING_PM_REVIEW)
        sim.invoice_status(ready, InvoiceStatus.READY_TO_PAY)

    pending = _round(sim, opportunity, workers, LAST_ROUND_DAYS_AGO, per_worker=1 if small else 2, over_days=2)
    with sim.at(_common.days_ago(LAST_ROUND_DAYS_AGO - 1, hour=15)):
        sim.invoice_status(pending, InvoiceStatus.PENDING_PM_REVIEW)
    _common.link_page(sim, opportunity, "invoices", "opportunity:invoice_list")


def _round(sim, opportunity, workers, delivered_days_ago, *, per_worker, over_days):
    """Deliver the round's visits, which are approved on arrival, and invoice them the next day."""
    with sim.at(_common.days_ago(delivered_days_ago, hour=ROUND_END_HOUR)):
        sim.deliver(opportunity, workers, per_worker=per_worker, over_days=over_days)
    with sim.at(_common.days_ago(delivered_days_ago - 1)):
        return sim.invoice(opportunity)
