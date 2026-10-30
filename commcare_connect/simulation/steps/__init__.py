"""The step vocabulary scenarios are written in.

Every step creates data by sending the request a real user action sends: a page's form submit as the acting
admin (``Sim.browser``), or the mobile API call as the worker. Inbound integrations that have no page, such as HQ
forwarding a submitted form, call the function their endpoint runs. Steps never write rows directly.

Each step runs in its own transaction so ``on_commit`` side effects (invites, accrual, notifications) exist before
the next step starts. Run steps inside ``fakes.local_fakes()``.
"""

import random
from contextlib import contextmanager, nullcontext

from django.db import transaction

from .browser import Browser
from .payments import PaymentSteps
from .review import ReviewSteps
from .setup import SetupSteps
from .types import PaymentUnitSpec
from .workers import WorkerSteps

__all__ = ["Sim", "PaymentUnitSpec"]


class Sim(SetupSteps, WorkerSteps, ReviewSteps, PaymentSteps):
    """Acts as ``pm_admin`` for program-manager steps and ``nm_admin`` for network-manager steps.

    Both may be the same user, who then sees each side of a managed opportunity by switching organization.
    """

    def __init__(self, *, pm_admin, nm_admin, seed=42, log=print):
        self.pm_admin = pm_admin
        self.nm_admin = nm_admin
        self.rng = random.Random(seed)
        self.log = log
        self.links = []
        self._browsers = {}
        # Wraps every step. Tests replace it with django_capture_on_commit_callbacks(execute=True), because the
        # test transaction never commits and on_commit side effects would otherwise never run.
        self.commit_scope = nullcontext

    @contextmanager
    def step(self):
        """Run a step's body in its own transaction; its on_commit callbacks run when the step ends."""
        with self.commit_scope(), transaction.atomic():
            yield

    def browser(self, user):
        """``user``'s logged-in session in the web app, for submitting pages as them."""
        if user.pk not in self._browsers:
            self._browsers[user.pk] = Browser(user)
        return self._browsers[user.pk]

    def link(self, label, url):
        """Record a page worth opening, once; ``run_scenario`` prints them when the scenario finishes."""
        if (label, url) not in self.links:
            self.links.append((label, url))
