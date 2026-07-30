from datetime import date

import pytest

from commcare_connect.opportunity.opportunity_header import _delivery_window, _pct


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
