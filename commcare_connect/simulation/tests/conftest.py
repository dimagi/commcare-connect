import pytest

from commcare_connect.simulation.fakes import local_fakes
from commcare_connect.simulation.steps import Sim


@pytest.fixture
def sim(user, django_capture_on_commit_callbacks):
    """A ``Sim`` acting as ``user`` on both sides, with the stand-ins active and on_commit side effects run."""
    sim = Sim(pm_admin=user, nm_admin=user, log=lambda *args: None)
    sim.commit_scope = lambda: django_capture_on_commit_callbacks(execute=True)
    with local_fakes():
        yield sim
