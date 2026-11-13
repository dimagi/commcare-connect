import pytest

from commcare_connect.simulation.scenarios import get_scenario, scenario_names


@pytest.mark.django_db
@pytest.mark.parametrize("name", scenario_names())
def test_scenario_builds(sim, name):
    """Every standard scenario must build at its smallest size; a failure here means a step fell behind the app."""
    get_scenario(name).build(sim, small=True)
