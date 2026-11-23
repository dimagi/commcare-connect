"""Data for the header's organization / program / opportunity selector.

The selector lists every organization the user belongs to, the programs each one can see,
and the opportunities under each program, with visit totals at every level.
"""

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from functools import partial
from typing import Any, Self

from django.contrib.humanize.templatetags.humanize import intcomma
from django.core.cache import cache
from django.db import transaction
from django.db.models import Count, F, Max
from django.urls import reverse
from django.utils.timezone import now
from django.utils.translation import gettext, ngettext

from commcare_connect.opportunity.filters import FilterMixin
from commcare_connect.opportunity.models import Opportunity, UserVisit
from commcare_connect.organization.decorators import user_can_edit_org_profile
from commcare_connect.organization.models import Organization, UserOrganizationMembership
from commcare_connect.program.models import Program
from commcare_connect.program.utils import (
    opportunities_accessible_to_orgs,
    org_access_for_program,
    org_opportunity_access,
    programs_accessible_to_orgs,
)
from commcare_connect.users.models import User

# Visit stats are cached per opportunity and shared by every user. A new visit clears its
# opportunity's entry (clear_visit_stats_cache); the timeout covers visits deleted or moved elsewhere.
VISIT_STATS_CACHE_TIMEOUT = 15 * 60


@dataclass
class VisitStats:
    count: int = 0
    last_visit_date: datetime | None = None

    @classmethod
    def total(cls, stats: Iterable[Self]) -> Self:
        stats = list(stats)
        dates = [s.last_visit_date for s in stats if s.last_visit_date]
        return cls(count=sum(s.count for s in stats), last_visit_date=max(dates, default=None))

    def summary(self) -> str:
        if not self.count:
            return gettext("No visits yet")
        visits = ngettext("%(count)s visit", "%(count)s visits", self.count) % {"count": intcomma(self.count)}
        if not self.last_visit_date:
            return visits
        last = self.last_visit_date.strftime("%d %b, %Y")
        return gettext("%(visits)s · Last visit %(date)s") % {"visits": visits, "date": last}


def end_label(end_date: date | None) -> str:
    """Hover text for a program or opportunity: when it ends, or when it ended."""
    if not end_date:
        return gettext("No end date")
    formatted = end_date.strftime("%d %b, %Y")
    if end_date < now().date():
        return gettext("Ended %(date)s") % {"date": formatted}
    return gettext("Ends %(date)s") % {"date": formatted}


def opportunity_end_label(opportunity: Opportunity) -> str:
    """Hover text for an opportunity: its end date, plus why it is inactive when the end date doesn't say."""
    label = end_label(opportunity.end_date)
    if opportunity.is_test:
        return gettext("Test opportunity · %(label)s") % {"label": label}
    if not opportunity.is_active and not opportunity.has_ended:
        return gettext("Inactive · %(label)s") % {"label": label}
    return label


def selector_tree(user: User) -> list[dict[str, Any]]:
    """Return the organizations the user is a member of, each with its programs and their opportunities."""
    memberships = (
        UserOrganizationMembership.objects.filter(user=user)
        .select_related("organization")
        .order_by("organization__name")
    )
    orgs = {membership.organization: membership for membership in memberships}
    opportunities_by_org = _opportunities_by_org(orgs)
    filterable_programs_by_org = _filterable_programs_by_org(orgs)
    stats = _visit_stats({opp.id for opps in opportunities_by_org.values() for opp in opps})
    return [
        _org_node(
            org=org,
            opportunities={opp: stats[opp.id] for opp in opportunities_by_org[org]},
            filterable_programs=filterable_programs_by_org[org],
            can_open_org_home=user_can_edit_org_profile(user, membership),
        )
        for org, membership in orgs.items()
    ]


def _opportunities_by_org(orgs: Iterable[Organization]) -> dict[Organization, list[Opportunity]]:
    """Each org's unarchived opportunities, latest end date first; those without one come first.

    One query fetches the opportunities of all the orgs together; the loop works out which org each one belongs to.
    """
    opportunities = (
        opportunities_accessible_to_orgs(orgs)
        .filter(archived=False)
        .select_related("program")
        .prefetch_related("program__watchers")
        .order_by(F("end_date").desc(nulls_first=True), "name")
    )
    by_org = {org: [] for org in orgs}
    for opp in opportunities:
        for org, opps in by_org.items():
            if org_opportunity_access(org, opp).has_view:
                opps.append(opp)
    return by_org


def _filterable_programs_by_org(orgs: Iterable[Organization]) -> dict[Organization, dict[int, Program]]:
    """The programs each org runs, funds or watches, by id. Only these work as the opportunity list's program filter.

    One query fetches the programs of all the orgs together; the loop works out which org each one belongs to.
    """
    programs = programs_accessible_to_orgs(orgs).prefetch_related("watchers")
    by_org = {org: {} for org in orgs}
    for program in programs:
        for org, org_programs in by_org.items():
            if org_access_for_program(org, program).has_view:
                org_programs[program.id] = program
    return by_org


def _org_node(
    org: Organization,
    opportunities: dict[Opportunity, VisitStats],
    filterable_programs: dict[int, Program],
    can_open_org_home: bool,
) -> dict[str, Any]:
    """`opportunities` maps each of the org's opportunities to its visit stats."""
    opportunities_by_program = defaultdict(dict)
    for opp, opp_stats in opportunities.items():
        opportunities_by_program[opp.program_id][opp] = opp_stats
    # The programs of the opportunities it delivers are listed too.
    programs = {**filterable_programs, **{opp.program_id: opp.program for opp in opportunities}}

    home_url = "organization:home" if can_open_org_home else "opportunity:list"
    return {
        "id": org.slug,
        "name": org.name,
        "type": gettext("Program Manager") if org.program_manager else gettext("Network Manager"),
        "test": org.is_test,
        "url": reverse(home_url, args=(org.slug,)),
        "opportunities_url": _opportunity_list_url(org=org, params={}),
        "programs": [
            _program_node(
                org=org,
                program=program,
                opportunities=opportunities_by_program[program.id],
                opportunities_url=_opportunity_list_url(
                    org=org,
                    params={"program": program.slug} if program.id in filterable_programs else {},
                ),
            )
            # Latest end date first, so running programs sit above ended ones.
            for program in sorted(programs.values(), key=lambda p: (-p.end_date.toordinal(), p.name))
        ],
        "visit_summary": VisitStats.total(opportunities.values()).summary(),
    }


def _opportunity_list_url(org: Organization, params: dict[str, str]) -> str:
    """The org's opportunity list for "See all", showing what the selector shows.

    Marking the filters as chosen turns off the list's defaults, which hide test and inactive opportunities.
    """
    return reverse(
        "opportunity:list",
        args=(org.slug,),
        query={**params, FilterMixin.filters_submitted_param: "1"},
    )


def _program_node(
    org: Organization,
    program: Program,
    opportunities: dict[Opportunity, VisitStats],
    opportunities_url: str,
) -> dict[str, Any]:
    opportunity_nodes = [
        _opportunity_node(org=org, opportunity=opp, stats=opp_stats) for opp, opp_stats in opportunities.items()
    ]
    return {
        "id": str(program.program_id),
        "name": program.name,
        # A program past its end date still counts while one of its opportunities runs.
        "active": program.end_date >= now().date() or any(node["active"] for node in opportunity_nodes),
        "url": opportunities_url,
        "opportunities_url": opportunities_url,
        "opportunities": opportunity_nodes,
        "end_label": end_label(program.end_date),
        "visit_summary": VisitStats.total(opportunities.values()).summary(),
    }


def _opportunity_node(org: Organization, opportunity: Opportunity, stats: VisitStats) -> dict[str, Any]:
    return {
        "id": str(opportunity.opportunity_id),
        "name": opportunity.name,
        # Active matches the opportunity list's default filters, which also leave out test opportunities.
        "active": opportunity.is_active and not opportunity.is_test,
        "test": opportunity.is_test,
        "url": reverse("opportunity:detail", args=(org.slug, opportunity.opportunity_id)),
        "end_label": opportunity_end_label(opportunity),
        "visit_summary": stats.summary(),
    }


def _visit_stats(opportunity_ids: Iterable[int]) -> defaultdict[int, VisitStats]:
    """Visit count and latest visit per opportunity: from the cache, with one query for the rest."""
    keys = {_visit_stats_cache_key(opp_id): opp_id for opp_id in opportunity_ids}
    stats = defaultdict(VisitStats, {keys[key]: value for key, value in cache.get_many(keys).items()})
    missing = [opp_id for opp_id in keys.values() if opp_id not in stats]
    if not missing:
        return stats
    rows = (
        UserVisit.objects.filter(opportunity_id__in=missing)
        .values("opportunity_id")
        .annotate(count=Count("id"), last=Max("visit_date"))
    )
    fresh = {opp_id: VisitStats() for opp_id in missing}
    for row in rows:
        fresh[row["opportunity_id"]] = VisitStats(row["count"], row["last"])
    cache.set_many(
        {_visit_stats_cache_key(opp_id): value for opp_id, value in fresh.items()}, timeout=VISIT_STATS_CACHE_TIMEOUT
    )
    stats.update(fresh)
    return stats


def clear_visit_stats_cache(opportunity_id: int) -> None:
    """Drop the opportunity's cached visit stats once the current transaction commits.

    Clearing after the commit stops a selector load that runs in between from caching the old totals again.
    """
    transaction.on_commit(partial(cache.delete, _visit_stats_cache_key(opportunity_id)))


def _visit_stats_cache_key(opportunity_id: int) -> str:
    return f"context_selector:visit_stats:{opportunity_id}"
