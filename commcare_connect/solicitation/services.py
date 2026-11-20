from django.db import transaction
from django.utils.timezone import now

from commcare_connect.solicitation.models import SolicitationApplication
from commcare_connect.solicitation.tasks import send_application_submitted_email


class ApplicationNotEditable(Exception):
    """The application was already sent, so its answers can't change."""


@transaction.atomic
def save_application(*, solicitation, organization, answers, user, submit):
    """Saves `organization`'s application to `solicitation` with the answers in `answers`, as `user`'s.

    `answers` is a validated ApplicationAnswersForm. The application row is created only here, so a form that
    fails validation leaves nothing behind. Submitting sends the application and emails `user` a confirmation once
    committed. Raises ApplicationNotEditable if the application was already sent.

    The submitter's details are copied, so they stay as they were when they applied even if the user changes.
    """
    application = _locked_application(solicitation=solicitation, organization=organization, user=user)
    if application.status != SolicitationApplication.Status.DRAFT:
        raise ApplicationNotEditable()
    application.submitted_by = user
    application.submitter_name = user.name or user.email
    application.submitter_email = user.email
    application.modified_by = user.email
    if submit:
        application.status = SolicitationApplication.Status.SUBMITTED
        application.submitted_date = now()
    application.save()
    answers.save(application=application, user_email=user.email)
    if submit:
        send_application_submitted_email.delay_on_commit(application.pk)
    return application


def _locked_application(*, solicitation, organization, user):
    """Locked until the transaction commits, so a second click on Submit waits, then finds it already sent."""
    application, _created = SolicitationApplication.objects.get_or_create(
        solicitation=solicitation,
        organization=organization,
        defaults={"created_by": user.email},
    )
    return SolicitationApplication.objects.select_for_update().get(pk=application.pk)
