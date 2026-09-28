from datetime import date

import pytest

from commcare_connect.utils.datetime import parse_year_month


@pytest.mark.parametrize(
    "value, expected",
    [
        ("2026-06", date(2026, 6, 1)),
        ("", None),
        (None, None),
        ("June 2026", None),
        ("2026-13", None),
    ],
)
def test_parse_year_month(value, expected):
    assert parse_year_month(value) == expected
