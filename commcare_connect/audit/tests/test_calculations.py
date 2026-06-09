from commcare_connect.audit import calculations
from commcare_connect.audit.calculations import AuditCalculation, CalculationResult, register_calculation


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
        return self._value, self._sample_size


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


def test_register_calculation_appends_instance():
    original = list(calculations._REGISTRY)
    try:
        calculations._REGISTRY.clear()

        @register_calculation
        class Dummy(AuditCalculation):
            name = "dummy"
            label = "Dummy"

            def compute(self, opportunity_access, period_start, period_end):
                return 1, 1

        registered = calculations.get_registered_calculations()
        assert len(registered) == 1
        assert isinstance(registered[0], Dummy)
    finally:
        calculations._REGISTRY[:] = original
