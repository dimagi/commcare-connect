import pytest

from commcare_connect.audit import calculations
from commcare_connect.audit.calculations import (
    AuditCalculation,
    CalculationResult,
    Measurement,
    format_value,
    register_calculation,
)


@pytest.mark.parametrize(
    "result, with_fraction, expected",
    [
        ({"value": None}, False, "-"),
        ({"value": 0.564356}, False, "0.56"),
        ({"value": 0.5}, False, "0.50"),
        ({"value": 12.0}, False, "12.00"),
        ({"value": 56.44543, "numerator": 56, "denominator": 100}, False, "56%"),
        ({"value": 56.6, "numerator": 57, "denominator": 100}, False, "57%"),
        ({"value": 3}, False, "3"),
        ({"value": "n/a"}, False, "n/a"),
        ({"value": 56.44543, "numerator": 3, "denominator": 5}, True, "56% (3/5)"),
        ({"value": 0.564356}, True, "0.56"),
        ({"value": None}, True, "-"),
        (
            {
                "value": 0.8,
                "components": [
                    {"numerator": 1, "denominator": 2},
                    {"numerator": 10, "denominator": 20},
                ],
            },
            False,
            "0.80 ((1/2) / (10/20))",
        ),
        (
            {
                "value": 0.8,
                "components": [
                    {"numerator": 1, "denominator": 2},
                    {"numerator": 10, "denominator": 20},
                ],
            },
            True,
            "0.80 ((1/2) / (10/20))",
        ),
    ],
)
def test_format_value(result, with_fraction, expected):
    assert format_value(result, with_fraction=with_fraction) == expected


def test_calculation_result_to_dict():
    result = CalculationResult(
        name="foo",
        label="Foo",
        value=0.5,
        has_sufficient_data=True,
        in_range=False,
    )
    assert result.to_dict() == {
        "value": 0.5,
        "has_sufficient_data": True,
        "in_range": False,
        "label": "Foo",
    }


class _FakeCalc(AuditCalculation):
    name = "fake"
    label = "Fake"
    min_sample_size = 3
    lower_bound = 0.5
    upper_bound = 0.9

    def __init__(self, value, sample_size):
        self._value = value
        self._sample_size = sample_size

    def compute(self, opportunity_access, period_start, period_end):
        return Measurement(self._value, self._sample_size)


def _run(value, sample_size):
    return _FakeCalc(value, sample_size).run(None, None, None)


def test_insufficient_data_marks_in_range_true_and_value_none():
    result = _run(0.4, sample_size=2)  # below min_sample_size=3
    assert result.has_sufficient_data is False
    assert result.value is None
    assert result.in_range is True


def test_value_below_lower_bound_is_out_of_range():
    result = _run(0.4, sample_size=5)
    assert result.has_sufficient_data is True
    assert result.value == 0.4
    assert result.in_range is False


def test_value_above_upper_bound_is_out_of_range():
    result = _run(0.95, sample_size=5)
    assert result.in_range is False


def test_value_inside_bounds_is_in_range():
    result = _run(0.7, sample_size=5)
    assert result.in_range is True


class _PctCalc(AuditCalculation):
    name = "pct"
    label = "Pct"
    is_percentage = True
    min_sample_size = 5

    def __init__(self, measurement):
        self._measurement = measurement

    def compute(self, opportunity_access, period_start, period_end):
        return self._measurement


class _RatioCalc(AuditCalculation):
    name = "ratio"
    label = "Ratio"
    min_sample_size = 1

    def compute(self, opportunity_access, period_start, period_end):
        return Measurement(
            0.8,
            2,
            components=[
                {"numerator": 1, "denominator": 2},
                {"numerator": 10, "denominator": 20},
            ],
        )


def test_components_flow_through_run_and_to_dict():
    result = _RatioCalc().run(None, None, None)
    components = [
        {"numerator": 1, "denominator": 2},
        {"numerator": 10, "denominator": 20},
    ]
    assert result.components == components
    assert result.to_dict()["components"] == components


def test_percentage_denominator_defaults_to_sample_size():
    result = _PctCalc(Measurement(25.0, 8)).run(None, None, None)
    assert result.denominator == 8
    assert result.numerator == 2  # round(25.0 * 8 / 100)


def test_percentage_denominator_override_decouples_from_gating_sample():
    # Gate on sample_size=6 (>= min 5), but report the rate over 8.
    result = _PctCalc(Measurement(25.0, 6, denominator=8)).run(None, None, None)
    assert result.has_sufficient_data is True
    assert result.denominator == 8
    assert result.numerator == 2  # round(25.0 * 8 / 100), derived from value not sample_size


def test_register_calculation_appends_instance():
    original = list(calculations._REGISTRY)
    try:
        calculations._REGISTRY.clear()

        @register_calculation
        class Dummy(AuditCalculation):
            name = "dummy"
            label = "Dummy"

            def compute(self, opportunity_access, period_start, period_end):
                return Measurement(1, 1)

        registered = calculations.get_registered_calculations()
        assert len(registered) == 1
        assert isinstance(registered[0], Dummy)
    finally:
        calculations._REGISTRY[:] = original
