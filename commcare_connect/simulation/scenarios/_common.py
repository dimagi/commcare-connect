"""The opening every standard scenario shares: its two organizations, a program, and the moments history runs on.

Scenarios build history backwards from now: the program about six weeks ago, opportunities a month ago, workers
in the weeks after, and reviews, payments and invoices in the last days.
"""

import datetime

from django.urls import reverse
from django.utils.timezone import now

PROGRAM_DAYS_AGO = 45
JOIN_DAYS_AGO = 42
OPPORTUNITY_DAYS_AGO = 32
INVITE_DAYS_AGO = 28
LEARN_DAYS_AGO = 24
CLAIM_DAYS_AGO = 23


def org_names(title):
    """The program-manager and network-manager organizations of the scenario called ``title``."""
    return [f"Sim {title} PM", f"Sim {title} NM"]


def days_ago(days, hour=10):
    """``hour`` o'clock UTC, ``days`` days before today. Keep ``days`` at least 1, so the moment is in the past."""
    day = now().date() - datetime.timedelta(days=days)
    return datetime.datetime.combine(day, datetime.time(hour), tzinfo=datetime.UTC)


def program(sim, title):
    """Create the scenario's organizations and program, and have the network manager join it.

    Returns ``(program, nm_org)``.
    """
    pm_name, nm_name = org_names(title)
    with sim.at(days_ago(PROGRAM_DAYS_AGO)):
        pm_org = sim.org(pm_name, admin=sim.pm_admin, program_manager=True)
        nm_org = sim.org(nm_name, admin=sim.nm_admin)
        created = sim.program(pm_org, name=f"{title} Program")
    with sim.at(days_ago(JOIN_DAYS_AGO)):
        sim.join_program(created, nm_org)
    return created, nm_org


def opportunity(sim, program, nm_org, name, days=OPPORTUNITY_DAYS_AGO, **options):
    """Create and launch a managed opportunity ``days`` ago; it starts that day unless ``options`` say otherwise."""
    with sim.at(days_ago(days)):
        return sim.opportunity(program, nm_org, name=name, **options)


def staffed_opportunity(sim, title, workers, **options):
    """Set up ``title``'s program and its "<title> Opportunity", with ``workers`` trained workers who have claimed it.

    ``options`` go to ``Sim.opportunity``. Returns ``(opportunity, workers)``.
    """
    program_, nm_org = program(sim, title)
    created = opportunity(sim, program_, nm_org, f"{title} Opportunity", **options)
    return created, trained_workers(sim, created, workers)


def trained_workers(sim, opportunity, count, *, claim=True):
    """Invite ``count`` workers, have them finish learning in the following days and, if ``claim``, claim."""
    with sim.at(days_ago(INVITE_DAYS_AGO)):
        workers = sim.invite(opportunity, count)
    with sim.at(days_ago(LEARN_DAYS_AGO)):
        sim.learn(opportunity, workers)
    if claim:
        with sim.at(days_ago(CLAIM_DAYS_AGO)):
            sim.claim(opportunity, workers)
    link_page(sim, opportunity, "workers", "opportunity:worker_list")
    return workers


def link_page(sim, opportunity, label, url_name):
    """Record the network manager's ``url_name`` page of ``opportunity`` as worth opening."""
    sim.link(
        f"{opportunity.name}: {label}",
        reverse(url_name, args=(opportunity.organization.slug, opportunity.opportunity_id)),
    )
