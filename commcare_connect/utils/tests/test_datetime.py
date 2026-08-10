from datetime import date

import pytest

from commcare_connect.utils.datetime import get_elapsed_percent, get_months_remaining


@pytest.mark.parametrize(
    ("start_date", "end_date", "today", "expected"),
    [
        (date(2026, 8, 1), date(2026, 12, 31), date(2026, 7, 1), 0),  # not yet started
        (date(2026, 1, 1), date(2026, 1, 11), date(2026, 1, 6), 50),  # halfway through
        (date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 2), 100),  # ends today
        (date(2026, 1, 1), date(2026, 3, 1), date(2026, 4, 1), 100),  # already past end_date
        (date(2026, 1, 1), date(2026, 1, 1), date(2026, 1, 1), 100),  # zero-length window
    ],
)
def test_get_elapsed_percent(start_date, end_date, today, expected):
    assert get_elapsed_percent(start_date, end_date, today) == expected


@pytest.mark.parametrize(
    ("end_date", "today", "expected"),
    [
        (date(2026, 12, 31), date(2026, 7, 1), 5),
        (date(2026, 1, 11), date(2026, 1, 6), 0),
        # End day (15) is earlier in the month than today's day (20), so the partial month
        # doesn't count as a whole month.
        (date(2026, 3, 15), date(2026, 1, 20), 1),
        (date(2026, 1, 1), date(2026, 1, 1), 0),
        (date(2020, 1, 1), date(2026, 1, 1), 0),  # end_date in the past floors at 0, not negative
    ],
)
def test_get_months_remaining(end_date, today, expected):
    assert get_months_remaining(end_date, today) == expected
