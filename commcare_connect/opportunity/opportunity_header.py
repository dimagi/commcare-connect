import datetime

from django.db.models import Count, Q, Sum
from django.utils.translation import gettext_lazy as _

from commcare_connect.opportunity.models import DeliverUnit, LearnModule, Opportunity, OpportunityAccess


def get_opportunity_header_context(opportunity: Opportunity) -> dict:
    access_stats = OpportunityAccess.objects.filter(opportunity=opportunity).aggregate(
        workers_actual=Count("id", filter=Q(accepted=True)),
        budget_actual=Sum("payment_accrued"),
    )
    workers_actual = access_stats["workers_actual"]
    deliveries_actual = opportunity.approved_visits
    # number_of_users can be fractional when the budget doesn't divide evenly across workers;
    # floor it first so the service-deliveries cap scales off a whole worker count too, rather
    # than off number_of_users' raw fraction.
    workers_cap = int(opportunity.number_of_users)

    return {
        "ended_date": opportunity.end_date if opportunity.has_ended else None,
        "resources": [
            {
                "name": _("Learn App"),
                "tab": "Learn App",
                "icon": "fa-book-open",
                "count": LearnModule.objects.filter(app=opportunity.learn_app).count(),
            },
            {
                "name": _("Deliver App"),
                "tab": "Deliver App",
                "icon": "fa-clipboard-check",
                "count": DeliverUnit.objects.filter(app=opportunity.deliver_app).count(),
            },
            {
                "name": _("Payment Units"),
                "tab": "Payments Units",
                "icon": "fa-hand-holding-dollar",
                "count": opportunity.paymentunit_set.count(),
            },
        ],
        "window": _delivery_window(opportunity.start_date, opportunity.end_date, datetime.date.today()),
        "metrics": [
            {"label": _("Connect Workers"), **_ratio(workers_actual, workers_cap)},
            {
                "label": _("Service Deliveries"),
                **_ratio(deliveries_actual, workers_cap * opportunity.max_visits_per_user),
            },
        ],
        "budget": _ratio(access_stats["budget_actual"] or 0, opportunity.total_budget),
    }


def _ratio(actual, cap):
    return {"actual": actual, "cap": cap, "pct": _pct(actual, cap)}


def _pct(actual, cap):
    if not cap:
        return 0
    return max(0, min(100, round(actual / cap * 100)))


def _delivery_window(start_date, end_date, today):
    if not start_date or not end_date:
        return {"pct": 0, "closed": False, "months_left": None}

    closed = end_date < today
    if closed:
        pct = 100
    elif start_date >= today:
        pct = 0
    else:
        # Reached only when start_date < today <= end_date, so the window always spans
        # at least one day here.
        total_days = (end_date - start_date).days
        elapsed_days = (today - start_date).days
        pct = _pct(elapsed_days, total_days)

    months_left = None
    if not closed:
        months_left = (end_date.year - today.year) * 12 + (end_date.month - today.month)
        if end_date.day < today.day:
            months_left -= 1
        months_left = max(months_left, 1)

    return {"pct": pct, "closed": closed, "months_left": months_left}
