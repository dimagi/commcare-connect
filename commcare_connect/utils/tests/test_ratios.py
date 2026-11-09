import pytest

from commcare_connect.utils.ratios import pct, ratio


@pytest.mark.parametrize(
    "numerator, denominator, ndigits, expected",
    [
        (1, 3, 2, 33.33),
        (1, 3, None, 33),
        (0, 5, 2, 0.0),
        (1, 0, 2, None),
        (1, None, 2, None),
        (None, 5, 2, None),
    ],
)
def test_pct(numerator, denominator, ndigits, expected):
    assert pct(numerator, denominator, ndigits=ndigits) == expected


@pytest.mark.parametrize(
    "numerator, denominator, expected",
    [
        (40, 50, 0.8),
        (1, 3, 0.33),
        (1, 0, None),
        (None, 5, None),
    ],
)
def test_ratio(numerator, denominator, expected):
    assert ratio(numerator, denominator) == expected
