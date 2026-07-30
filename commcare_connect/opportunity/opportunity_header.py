import datetime

from django.db.models import Sum

from commcare_connect.opportunity.models import DeliverUnit, LearnModule, Opportunity, OpportunityAccess

# Tailwind utility classes for every themed element of the opportunity header.
# "live"/"test" = surface theme (Opportunity.is_test), "active"/"inactive" = lifecycle modifier.
_THEME = {
    "live": {
        "active": {
            "hero": "bg-brand-deep-purple",
            "overlay": False,
            "eyebrow": "text-brand-sky",
            "title": "text-white",
            "description": "text-white/72",
            "well": "bg-black/20",
            "divider": "border-white/20",
            "track": "bg-white/18",
            "value": "text-white",
            "subvalue": "text-white/55",
            "counter_bg": "bg-white/10",
            "counter_border": "border-white/22",
            "counter_text": "text-white",
            "counter_icon": "text-brand-sky",
            "btn_border": "border-white/28",
            "btn_bg": "bg-white/8",
            "btn_text": "text-white",
            "btn_hover": "hover:bg-white/20",
            "bar_fill": "bg-brand-sky",
            "bar_fill_budget": "bg-brand-marigold",
            "ended_note": "text-white/50",
        },
        "inactive": {
            "hero": "bg-brand-deep-purple",
            "overlay": True,
            "eyebrow": "text-brand-sky",
            "title": "text-white/82",
            "description": "text-white/60",
            "well": "bg-black/20",
            "divider": "border-white/20",
            "track": "bg-white/16",
            "value": "text-white/85",
            "subvalue": "text-white/50",
            "counter_bg": "bg-white/8",
            "counter_border": "border-white/18",
            "counter_text": "text-white/80",
            "counter_icon": "text-brand-sky",
            "btn_border": "border-white/28",
            "btn_bg": "bg-white/8",
            "btn_text": "text-white",
            "btn_hover": "hover:bg-white/20",
            "bar_fill": "bg-white/45",
            "bar_fill_budget": "bg-white/45",
            "ended_note": "text-white/50",
        },
    },
    "test": {
        "active": {
            "hero": "bg-white border border-brand-border-light",
            "overlay": False,
            "eyebrow": "text-brand-indigo",
            "title": "text-brand-deep-purple",
            "description": "text-gray-500",
            "well": "bg-slate-50",
            "divider": "border-slate-200",
            "track": "bg-slate-200",
            "value": "text-brand-deep-purple",
            "subvalue": "text-slate-400",
            "counter_bg": "bg-brand-indigo",
            "counter_border": "border-brand-indigo",
            "counter_text": "text-white",
            "counter_icon": "text-white",
            "btn_border": "border-gray-400",
            "btn_bg": "bg-gray-50",
            "btn_text": "text-brand-deep-purple",
            "btn_hover": "hover:bg-slate-100",
            "bar_fill": "bg-brand-indigo",
            "bar_fill_budget": "bg-brand-marigold",
            "ended_note": "text-slate-400",
        },
        "inactive": {
            "hero": "bg-gray-50 border border-brand-border-light",
            "overlay": False,
            "eyebrow": "text-slate-400",
            "title": "text-gray-500",
            "description": "text-slate-400",
            "well": "bg-slate-100",
            "divider": "border-slate-200",
            "track": "bg-slate-200",
            "value": "text-gray-500",
            "subvalue": "text-slate-400",
            "counter_bg": "bg-indigo-100",
            "counter_border": "border-indigo-100",
            "counter_text": "text-indigo-600",
            "counter_icon": "text-indigo-600",
            "btn_border": "border-gray-400",
            "btn_bg": "bg-gray-50",
            "btn_text": "text-brand-deep-purple",
            "btn_hover": "hover:bg-slate-100",
            "bar_fill": "bg-slate-300",
            "bar_fill_budget": "bg-slate-300",
            "ended_note": "text-slate-400",
        },
    },
}


def get_opportunity_header_context(opportunity: Opportunity) -> dict:
    """Build the theme classes + metric strip data for the opportunity detail header partial."""
    is_inactive = not opportunity.is_active
    theme = _THEME["test" if opportunity.is_test else "live"]["inactive" if is_inactive else "active"]

    today = datetime.date.today()
    window = _delivery_window(opportunity.start_date, opportunity.end_date, today)

    workers_actual = OpportunityAccess.objects.filter(opportunity=opportunity, accepted=True).count()
    deliveries_actual = opportunity.approved_visits
    budget_actual = (
        OpportunityAccess.objects.filter(opportunity=opportunity).aggregate(total=Sum("payment_accrued"))["total"] or 0
    )

    return {
        "theme": theme,
        "is_inactive": is_inactive,
        "ended_date": opportunity.end_date if opportunity.has_ended else None,
        "resources": [
            {
                "name": "Learn App",
                "tab": "Learn App",
                "icon": "fa-book-open",
                "count": LearnModule.objects.filter(app=opportunity.learn_app).count(),
            },
            {
                "name": "Deliver App",
                "tab": "Deliver App",
                "icon": "fa-clipboard-check",
                "count": DeliverUnit.objects.filter(app=opportunity.deliver_app).count(),
            },
            {
                "name": "Payment Units",
                "tab": "Payments Units",
                "icon": "fa-hand-holding-dollar",
                "count": opportunity.paymentunit_set.count(),
            },
        ],
        "window": window,
        "workers": {
            "actual": workers_actual,
            "cap": opportunity.number_of_users,
            "pct": _pct(workers_actual, opportunity.number_of_users),
        },
        "deliveries": {
            "actual": deliveries_actual,
            "cap": opportunity.allotted_visits,
            "pct": _pct(deliveries_actual, opportunity.allotted_visits),
        },
        "budget": {
            "actual": budget_actual,
            "cap": opportunity.total_budget,
            "pct": _pct(budget_actual, opportunity.total_budget),
        },
    }


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
        total_days = (end_date - start_date).days
        elapsed_days = (today - start_date).days
        pct = 100 if total_days <= 0 else _pct(elapsed_days, total_days)

    months_left = None
    if not closed:
        months_left = (end_date.year - today.year) * 12 + (end_date.month - today.month)
        if end_date.day < today.day:
            months_left -= 1
        months_left = max(months_left, 1)

    return {"pct": pct, "closed": closed, "months_left": months_left}
