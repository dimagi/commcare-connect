from datetime import date
from unittest.mock import patch

import pytest
from django import forms

from commcare_connect.utils.datetime import DateRanges
from commcare_connect.utils.forms import PresetDateRangeField

TODAY = date(2026, 5, 31)


class RangeForm(forms.Form):
    period = PresetDateRangeField(presets=[DateRanges.LAST_30_DAYS, DateRanges.LAST_3_MONTHS, DateRanges.NEXT_30_DAYS])


def _clean(data):
    form = RangeForm(data=data)
    with patch("commcare_connect.utils.forms.localdate", return_value=TODAY):
        valid = form.is_valid()
    return valid, form


@pytest.mark.parametrize(
    "preset, expected",
    [
        ("last_30_days", slice(date(2026, 5, 1), TODAY)),
        ("last_3_months", slice(date(2026, 2, 28), TODAY)),
        ("next_30_days", slice(TODAY, date(2026, 6, 30))),
    ],
)
def test_a_preset_cleans_to_its_dates_and_ignores_custom_ones(preset, expected):
    valid, form = _clean({"period_range": preset, "period_from": "2020-01-01", "period_to": "2020-12-31"})

    assert valid
    assert form.cleaned_data["period"] == expected


@pytest.mark.parametrize(
    "data, expected",
    [
        ({}, None),
        ({"period_range": ""}, None),
        ({"period_from": "2026-01-01", "period_to": "2026-02-01"}, None),
        ({"period_range": "custom"}, None),
        ({"period_range": "custom", "period_from": "2026-01-01"}, slice(date(2026, 1, 1), None)),
        ({"period_range": "custom", "period_to": "2026-02-01"}, slice(None, date(2026, 2, 1))),
        (
            {"period_range": "custom", "period_from": "2026-01-01", "period_to": "2026-01-01"},
            slice(date(2026, 1, 1), date(2026, 1, 1)),
        ),
    ],
)
def test_custom_dates_apply_only_under_custom_range(data, expected):
    valid, form = _clean(data)

    assert valid
    assert form.cleaned_data["period"] == expected


@pytest.mark.parametrize(
    "data",
    [
        {"period_range": "last_12_months"},  # a range this field doesn't offer
        {"period_range": "custom", "period_from": "2026-02-01", "period_to": "2026-01-01"},
        {"period_range": "custom", "period_from": "not-a-date"},
    ],
)
def test_invalid_ranges_are_rejected(data):
    valid, form = _clean(data)

    assert not valid
    assert "period" in form.errors


def test_only_the_given_presets_are_offered_with_any_time_and_custom():
    choices = [value for value, _label in RangeForm().fields["period"].fields[0].choices]

    assert choices == ["", "last_30_days", "last_3_months", "next_30_days", "custom"]


def test_the_widget_keeps_the_submitted_values_and_shows_custom_dates_only_for_custom():
    html = RangeForm(data={"period_range": "custom", "period_from": "2026-01-01"})["period"].as_widget()

    assert 'name="period_range"' in html
    assert '<option value="custom" selected>' in html
    assert 'name="period_from" value="2026-01-01"' in html
    assert "x-show=\"preset === 'custom'\"" in html
