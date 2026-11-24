from django.urls import resolve, reverse
from django.utils.timezone import localtime
from waffle import switch_is_active

from commcare_connect.flags.switch_names import SOLICITATIONS
from commcare_connect.solicitation.models import Solicitation, SolicitationInvitation
from commcare_connect.solicitation.tasks import update_solicitation_statuses

from .form_data import management_form
from .setup import delivery_type as get_delivery_type

DEADLINE_FORMAT = "%Y-%m-%dT%H:%M"
# Weights must total 100 for the publish page to accept the solicitation.
CRITERIA = (("Technical approach", 60), ("Cost", 40))
QUESTIONS = (
    ("Describe how you would deliver this work.", "text"),
    ("How many frontline workers can you deploy?", "number"),
)


class SolicitationSteps:
    """RFPs and EOIs, created by submitting the program manager's solicitation pages as ``pm_admin``."""

    def solicitation(self, pm_org, spec, *, publish=True):
        """Create a solicitation owned by ``pm_org``, filled in from ``spec``, through the "New solicitation" pages.

        Submits the details, evaluation criteria and questions pages and, when ``publish`` is true, the review page's
        Publish button; otherwise it stays a draft. Returns the ``Solicitation``.
        """
        require_solicitations_switch()
        browser = self.browser(self.pm_admin)
        with self.step():
            response = browser.post(
                reverse("solicitation:create", args=(pm_org.slug,)),
                _details_data(spec),
            )
            solicitation = Solicitation.objects.get(solicitation_id=resolve(response.url).kwargs["solicitation_id"])
            step_args = (pm_org.slug, solicitation.solicitation_id)
            browser.post(reverse("solicitation:criteria", args=step_args), _criteria_data())
            browser.post(reverse("solicitation:questions", args=step_args), _questions_data(solicitation))
            if publish:
                browser.post(reverse("solicitation:publish", args=step_args))
            solicitation.refresh_from_db()
            self.log(f"Created {solicitation.get_status_display().lower()} solicitation {solicitation.title}")
        self.link(f"Solicitations of {pm_org.name}", reverse("solicitation:list", args=(pm_org.slug,)))
        return solicitation

    def invite_to_solicitation(self, solicitation, orgs):
        """Invite ``orgs`` to ``solicitation`` on its "Invited organizations" page; each org's members are emailed."""
        require_solicitations_switch()
        url = reverse("solicitation:invitations", args=(solicitation.organization.slug, solicitation.solicitation_id))
        with self.step():
            self.browser(self.pm_admin).post(url, {"organizations": [org.pk for org in orgs]})
            self.log(f"Invited {', '.join(org.slug for org in orgs)} to {solicitation.title}")
        return list(SolicitationInvitation.objects.filter(solicitation=solicitation, organization__in=orgs))

    def close_expired_solicitations(self):
        """Run the hourly task that closes solicitations whose deadline has passed, as Celery beat would.

        Like the real task it closes every expired solicitation in the database, not only the scenario's.
        """
        with self.step():
            update_solicitation_statuses()
            self.log("Closed expired solicitations")


def require_solicitations_switch():
    """Every solicitation page is behind the switch and answers 404 while it is off.

    Scenarios call it before their first step too, so they refuse before building anything.
    """
    if not switch_is_active(SOLICITATIONS):
        raise RuntimeError(
            f"The {SOLICITATIONS!r} waffle switch is off, so the solicitation pages don't exist. Turn it on with "
            f"`./manage.py waffle_switch {SOLICITATIONS} on --create`, then rerun."
        )


def _details_data(spec):
    """The details page as a program manager fills it in; ``public`` is its toggle, which posts only when on."""
    budget_min, budget_max = spec.budget
    data = {
        "title": spec.title,
        "type": spec.type,
        "description": f"{spec.title}: the scope of work, written by the simulation.",
        "budget_min": budget_min,
        "budget_max": budget_max,
        "currency": spec.currency,
        "country": spec.country,
        "delivery_type": get_delivery_type(spec.delivery_type).pk,
        # The page reads the deadline in the current time zone (UTC), so post it in that zone.
        "application_deadline": localtime(spec.deadline).strftime(DEADLINE_FORMAT),
        "estimated_scale": "5,000 households in 3 districts",
        "contact_email": "solicitations@simulation.local",
        "hide_scores_until_submit": "on",
    }
    if spec.public:
        data["public"] = "on"
    if spec.program:
        data["program"] = spec.program.pk
    return data


def _criteria_data():
    data = management_form("criteria", total=len(CRITERIA), max_num=1000)
    for index, (label, weight) in enumerate(CRITERIA):
        data |= {f"criteria-{index}-label": label, f"criteria-{index}-weight": weight}
    return data


def _questions_data(solicitation):
    """One question per criterion, each linked to the criterion it is scored against."""
    criteria = list(solicitation.criteria.order_by("pk"))
    data = management_form("questions", total=len(QUESTIONS), max_num=1000)
    for index, ((text, question_type), criterion) in enumerate(zip(QUESTIONS, criteria, strict=True)):
        data |= {
            f"questions-{index}-text": text,
            f"questions-{index}-question_type": question_type,
            f"questions-{index}-required": "on",
            f"questions-{index}-criterion": criterion.pk,
        }
    return data
