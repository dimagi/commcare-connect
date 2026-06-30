import pytest

from commcare_connect.microplanning.const import DEFAULT_BUILDING_COUNT
from commcare_connect.microplanning.forms import ClusterWorkAreasForm


class TestClusterWorkAreasForm:
    def test_empty_building_count_defaults_to_default(self):
        form = ClusterWorkAreasForm(data={})
        assert form.is_valid()
        assert form.cleaned_data["building_count"] == DEFAULT_BUILDING_COUNT

    @pytest.mark.parametrize("value", [100, 200, 300])
    def test_value_within_range_is_valid(self, value):
        form = ClusterWorkAreasForm(data={"building_count": value})
        assert form.is_valid()
        assert form.cleaned_data["building_count"] == value

    @pytest.mark.parametrize("value", [99, 301, 0, 1000])
    def test_value_out_of_range_is_invalid(self, value):
        form = ClusterWorkAreasForm(data={"building_count": value})
        assert not form.is_valid()
        assert "building_count" in form.errors
        assert "between 100 and 300" in str(form.errors["building_count"])
