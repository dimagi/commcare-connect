import pytest
from django.core.management import CommandError, call_command


@pytest.mark.django_db
def test_refuses_to_run_without_debug(settings):
    settings.DEBUG = False
    with pytest.raises(CommandError, match="--force"):
        call_command("run_scenario", "baseline")


@pytest.mark.django_db
def test_unknown_scenario(settings):
    settings.DEBUG = True
    with pytest.raises(CommandError, match="invalid choice"):
        call_command("run_scenario", "no_such_scenario", "--admin-email", "nobody@example.com")


@pytest.mark.django_db
def test_admin_must_be_an_existing_user(settings, monkeypatch):
    settings.DEBUG = True
    monkeypatch.setattr(
        "commcare_connect.simulation.management.commands.run_scenario.get_scenario", lambda name: object()
    )
    with pytest.raises(CommandError, match="No user with email"):
        call_command("run_scenario", "baseline", "--admin-email", "nobody@example.com")
