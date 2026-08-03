from datetime import date

import pytest

from commcare_connect.opportunity.opportunity_header import _delivery_window, _pct, get_opportunity_header_context
from commcare_connect.opportunity.tests.factories import OpportunityAccessFactory, PaymentUnitFactory


@pytest.mark.parametrize(
    ("actual", "cap", "expected"),
    [
        (0, 100, 0),
        (50, 100, 50),
        (100, 100, 100),
        (150, 100, 100),
        (-10, 100, 0),
        (1, 3, 33),
        (0, 0, 0),
        (5, None, 0),
    ],
)
def test_pct(actual, cap, expected):
    assert _pct(actual, cap) == expected


def test_delivery_window_missing_dates():
    assert _delivery_window(None, None, date(2026, 1, 1)) == {"pct": 0, "closed": False, "months_left": None}


def test_delivery_window_not_yet_started():
    window = _delivery_window(date(2026, 8, 1), date(2026, 12, 31), date(2026, 7, 1))
    assert window == {"pct": 0, "closed": False, "months_left": 5}


def test_delivery_window_closed():
    window = _delivery_window(date(2026, 1, 1), date(2026, 3, 1), date(2026, 4, 1))
    assert window == {"pct": 100, "closed": True, "months_left": None}


def test_delivery_window_in_progress():
    window = _delivery_window(date(2026, 1, 1), date(2026, 1, 11), date(2026, 1, 6))
    assert window == {"pct": 50, "closed": False, "months_left": 1}


def test_delivery_window_ends_today_is_not_closed():
    # end_date == today: matches Opportunity.has_ended (`end_date < today`), so this reads
    # as "ending today", not yet closed — and must not divide by a zero-length window.
    window = _delivery_window(date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 2))
    assert window == {"pct": 100, "closed": False, "months_left": 1}


def test_delivery_window_months_left_floors_to_whole_months_elapsed():
    # End day (15) is earlier in the month than today's day (20), so the partial month
    # doesn't count as a whole month left.
    window = _delivery_window(date(2026, 1, 1), date(2026, 3, 15), date(2026, 1, 20))
    assert window["months_left"] == 1


@pytest.mark.django_db
def test_get_opportunity_header_context(opportunity):
    # 1000 / (3 * 100) = 3.33... connect workers -- deliberately non-integral, since
    # number_of_users/allotted_visits are floats whenever the budget doesn't divide evenly.
    PaymentUnitFactory(opportunity=opportunity, max_total=3, amount=100, org_amount=0)
    opportunity.total_budget = 1000
    opportunity.save()

    OpportunityAccessFactory(opportunity=opportunity, accepted=True, payment_accrued=250)
    OpportunityAccessFactory(opportunity=opportunity, accepted=False, payment_accrued=0)

    context = get_opportunity_header_context(opportunity)

    connect_workers, service_deliveries = context["metrics"]
    assert connect_workers["actual"] == 1  # only the accepted access counts
    assert connect_workers["cap"] == 3  # int(3.33...) -- must floor, not render the raw fraction
    assert service_deliveries["cap"] == 9  # int(3 * 3.33...) == int(9.99...)

    assert context["budget"] == {"actual": 250, "cap": 1000, "pct": 25}
