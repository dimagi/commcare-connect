import sys

import pytest
from django.core.management import call_command

from commcare_connect.organization.models import Organization
from commcare_connect.simulation import scenarios
from commcare_connect.simulation.scenarios import local

PERSONAL_SCENARIO = """
from commcare_connect.simulation.scenarios import _common

ORG_NAMES = _common.org_names("Mine")


def build(sim, *, small):
    _common.program(sim, "Mine")
"""


@pytest.fixture
def local_dir(tmp_path, monkeypatch):
    """Point ``scenarios/local/`` at an empty folder, so the developer's own personal scenarios don't leak in."""
    monkeypatch.setattr(local, "__path__", [str(tmp_path)])
    yield tmp_path
    for name in [name for name in sys.modules if name.startswith(f"{local.__name__}.")]:
        del sys.modules[name]


def test_personal_scenario_runs_but_is_not_standard(local_dir):
    (local_dir / "mine.py").write_text(PERSONAL_SCENARIO)

    assert "mine" in scenarios.scenario_names()
    assert "mine" not in scenarios.standard_scenario_names()
    assert scenarios.get_scenario("mine").build


def test_local_package_is_not_a_scenario():
    assert "local" not in scenarios.scenario_names()


@pytest.mark.django_db
def test_run_scenario_builds_a_personal_scenario(local_dir, settings, user):
    settings.DEBUG = True
    (local_dir / "mine.py").write_text(PERSONAL_SCENARIO)

    call_command("run_scenario", "mine", "--admin-email", user.email)

    assert Organization.objects.filter(name="Sim Mine PM").exists()


def test_personal_scenario_may_not_reuse_a_standard_name(local_dir):
    (local_dir / "baseline.py").write_text(PERSONAL_SCENARIO)

    with pytest.raises(LookupError, match="baseline"):
        scenarios.scenario_names()
