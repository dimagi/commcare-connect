"""Deliveries with clean and flagged visits (duplicate, no GPS, too short), reviewed by the network manager
(approved, rejected, some left pending) and then by the program manager (agreed, disagreed)."""

from commcare_connect.simulation.constants import DELIVER_UNIT_SLUGS

from . import _common

TITLE = "Delivery Review"
ORG_NAMES = _common.org_names(TITLE)

# Each flagged form in the deliver step is 50 seconds long, so a 2-minute minimum flags it as too short.
VERIFICATION_FLAGS = {"duplicate": True, "gps": True}
DELIVER_UNIT_FLAGS = {DELIVER_UNIT_SLUGS[0]: {"duration": 2}}
REVIEWED_DAYS_AGO = 4
PM_REVIEWED_DAYS_AGO = 2
# Deliveries up to here are reviewed; later ones still wait for the network manager.
REVIEW_CUTOFF_DAYS_AGO = 6


def build(sim, *, small):
    opportunity, workers = _common.staffed_opportunity(
        sim,
        TITLE,
        2 if small else 10,
        verification_flags=VERIFICATION_FLAGS,
        deliver_unit_flags=DELIVER_UNIT_FLAGS,
    )
    flagged = {"duplicates": 1, "no_gps": 1, "too_short": 1}

    with sim.at(_common.days_ago(REVIEW_CUTOFF_DAYS_AGO, hour=19)):
        sim.deliver(opportunity, workers, per_worker=4 if small else 10, over_days=14, **flagged)
    with sim.at(_common.days_ago(REVIEWED_DAYS_AGO)):
        sim.review(opportunity, approve=0.5, reject=0.25)
    with sim.at(_common.days_ago(PM_REVIEWED_DAYS_AGO)):
        sim.pm_review(opportunity, agree=0.6)
    if not small:
        # Delivered at the real now, on purpose, after the reviews above have run: these visits cover the last few
        # days, and their flagged ones still wait for the network manager.
        sim.deliver(opportunity, workers, per_worker=4, over_days=REVIEW_CUTOFF_DAYS_AGO - 1, no_gps=1)
