from datetime import UTC, datetime, time, timedelta

from django.conf import settings
from django.urls import reverse
from django.utils.timezone import now
from rest_framework.test import APIClient

from commcare_connect.opportunity.forms import OpportunityUserInviteForm
from commcare_connect.opportunity.models import OpportunityAccess
from commcare_connect.opportunity.tasks import add_connect_users
from commcare_connect.simulation.constants import ASSESSMENT_SLUG, LEARN_MODULE_SLUGS
from commcare_connect.simulation.xforms import assessment_block, learn_module_block, submit_form
from commcare_connect.users.models import ConnectIDUserLink, User

# Fictional 555 numbers, so simulated workers never collide with real phone numbers.
PHONE_PREFIX = "+1555"


class WorkerSteps:
    """Inviting workers and what they do on the phone before delivering: claim and learn."""

    def invite(self, opportunity, count):
        """Invite ``count`` new ConnectID users by phone number. Returns the invited ``User``s.

        Validates the numbers with the invite form and runs the invite task the invite view queues.
        """
        with self.step():
            numbers = [f"{PHONE_PREFIX}{n:07d}" for n in self.rng.sample(range(10**7), count)]
            form = OpportunityUserInviteForm(data={"users": "\n".join(numbers)}, opportunity=opportunity)
            if not form.is_valid():
                raise ValueError(f"Invite form rejected for {opportunity}: {form.errors.as_text()}")
            add_connect_users.delay(form.cleaned_data["users"], opportunity.pk)
            users = User.objects.filter(opportunityaccess__opportunity=opportunity, phone_number__in=numbers)
            by_number = {user.phone_number: user for user in users}
            self.log(f"Invited {count} workers to {opportunity.name}")
        return [by_number[number] for number in numbers]

    def claim(self, opportunity, workers):
        """Claim the opportunity for each worker through the mobile claim API."""
        with self.step():
            for worker in workers:
                response = _phone(worker).post(f"/api/opportunity/{opportunity.opportunity_id}/claim")
                _check(response, worker, "claim")
            self.log(f"{len(workers)} workers claimed {opportunity.name}")

    def learn(self, opportunity, workers, *, complete=1.0, score=100):
        """Submit learn-app forms for each worker.

        ``complete`` is the share of learn modules each worker finishes; workers who finish every module also
        submit the assessment with ``score``. Each worker starts learning after their invite (and the
        opportunity's start) and finishes by the step's moment.
        """
        slugs = LEARN_MODULE_SLUGS[: round(complete * len(LEARN_MODULE_SLUGS))]
        blocks = [learn_module_block(slug) for slug in slugs]
        if len(slugs) == len(LEARN_MODULE_SLUGS):
            blocks.append(assessment_block(ASSESSMENT_SLUG, score))
        with self.step():
            for worker in workers:
                started, forms = self._learn_schedule(opportunity, worker, len(blocks))
                with self.at(started):
                    # Opening the learn app is what accepts the invite and creates the worker's CommCare user.
                    response = _phone(worker).post(
                        reverse("users:start_learn_app"), {"opportunity": str(opportunity.opportunity_id)}
                    )
                    _check(response, worker, "start learning")
                username = _commcare_username(worker, opportunity.learn_app)
                for block, (time_start, duration) in zip(blocks, forms):
                    # The phone sends each form when the worker finishes it.
                    with self.at(time_start + duration):
                        submit_form(opportunity.learn_app, username, block, time_start=time_start, duration=duration)
            self.log(f"{len(workers)} workers completed {len(slugs)}/{len(LEARN_MODULE_SLUGS)} learn modules")

    def _learn_schedule(self, opportunity, worker, form_count):
        """When the worker starts learning, and each form's ``(time_start, duration)``.

        Learning runs forward from the later of the invite and the opportunity's start. When the time from there
        to now is shorter than the drawn schedule, the whole schedule is compressed to fit.
        """
        access = OpportunityAccess.objects.get(user=worker, opportunity=opportunity)
        earliest = max(access.invited_date, datetime.combine(opportunity.start_date, time.min, tzinfo=UTC))
        window = now() - earliest
        if window < timedelta(0):
            raise ValueError(f"{worker.username} cannot learn {opportunity.name} before {earliest.isoformat()}")

        delay = timedelta(hours=self.rng.randint(1, 72))
        durations = [timedelta(minutes=self.rng.randint(5, 25)) for _ in range(form_count)]
        gaps = [timedelta(minutes=self.rng.randint(10, 240)) for _ in range(form_count - 1)] + [timedelta(0)]
        scale = min(1, window / (delay + sum(durations, timedelta()) + sum(gaps, timedelta())))

        started = earliest + delay * scale
        forms = []
        time_start = started
        for duration, gap in zip(durations, gaps):
            forms.append((time_start, duration * scale))
            time_start += (duration + gap) * scale
        return started, forms


def _commcare_username(worker, app):
    """The bare CommCare username, as HQ sends it; the form receiver adds the domain."""
    link = ConnectIDUserLink.objects.get(user=worker, domain=app.cc_domain, hq_server=app.hq_server)
    return link.commcare_username.split("@")[0]


def _phone(worker):
    """An API client authenticated as ``worker``, the way the Connect mobile app calls the server."""
    # The client's default host, "testserver", is only allowed while tests run; local settings allow localhost.
    client = APIClient(SERVER_NAME=settings.ALLOWED_HOSTS[0])
    # Stands in for the ConnectID OAuth token the mobile app sends.
    client.force_authenticate(worker)
    return client


def _check(response, worker, action):
    if response.status_code >= 300:
        raise RuntimeError(f"{worker.username} could not {action}: {response.status_code} {response.content!r}")
