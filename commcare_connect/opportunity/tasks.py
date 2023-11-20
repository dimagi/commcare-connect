import datetime

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.utils.timezone import now
from django.utils.translation import gettext

from commcare_connect.connect_id_client import fetch_users, send_message_bulk
from commcare_connect.connect_id_client.models import Message
from commcare_connect.opportunity.app_xml import get_connect_blocks_for_app, get_deliver_units_for_app
from commcare_connect.opportunity.export import export_empty_payment_table, export_user_visit_data
from commcare_connect.opportunity.forms import DateRanges
from commcare_connect.opportunity.models import (
    CompletedModule,
    DeliverUnit,
    LearnModule,
    Opportunity,
    OpportunityAccess,
    OpportunityClaim,
    UserVisit,
    VisitValidationStatus,
)
from commcare_connect.users.helpers import invite_user
from commcare_connect.users.models import User
from commcare_connect.utils.datetime import is_date_before
from config import celery_app


@celery_app.task()
def create_learn_modules_and_deliver_units(opportunity_id):
    opportunity = Opportunity.objects.filter(id=opportunity_id).first()
    learn_app = opportunity.learn_app
    deliver_app = opportunity.deliver_app
    learn_app_connect_blocks = get_connect_blocks_for_app(learn_app.cc_domain, learn_app.cc_app_id)
    deliver_app_connect_blocks = get_deliver_units_for_app(deliver_app.cc_domain, deliver_app.cc_app_id)

    for block in learn_app_connect_blocks:
        LearnModule.objects.update_or_create(
            app=learn_app,
            slug=block.id,
            defaults={
                "name": block.name,
                "description": block.description,
                "time_estimate": block.time_estimate,
            },
        )

    for block in deliver_app_connect_blocks:
        DeliverUnit.objects.update_or_create(app=deliver_app, slug=block.id, defaults=dict(name=block.name))


@celery_app.task()
def add_connect_users(user_list: list[str], opportunity_id: str):
    for user in fetch_users(user_list):
        u, _ = User.objects.update_or_create(
            username=user.username, defaults={"phone_number": user.phone_number, "name": user.name}
        )
        opportunity_access, _ = OpportunityAccess.objects.get_or_create(user=u, opportunity_id=opportunity_id)
        invite_user(u, opportunity_access)


@celery_app.task()
def generate_visit_export(opportunity_id: int, date_range: str, status: list[str], export_format: str):
    opportunity = Opportunity.objects.get(id=opportunity_id)
    dataset = export_user_visit_data(opportunity, DateRanges(date_range), [VisitValidationStatus(s) for s in status])
    content = dataset.export(export_format)
    export_tmp_name = f"{now().isoformat()}_{opportunity.name}_visit_export.{export_format}"
    if isinstance(content, str):
        content = content.encode()
    default_storage.save(export_tmp_name, ContentFile(content))
    return export_tmp_name


@celery_app.task()
def generate_payment_export(opportunity_id: int, export_format: str):
    opportunity = Opportunity.objects.get(id=opportunity_id)
    dataset = export_empty_payment_table(opportunity)
    content = dataset.export(export_format)
    export_tmp_name = f"{now().isoformat()}_{opportunity.name}_payment_export.{export_format}"
    if isinstance(content, str):
        content = content.encode()
    default_storage.save(export_tmp_name, ContentFile(content))
    return export_tmp_name


@celery_app.task()
def send_notification_inactive_users():
    opportunity_accesses = OpportunityAccess.objects.filter(
        opportunity__active=True,
        opportunity__end_date__gt=datetime.date.today(),
    ).select_related("opportunity")
    messages = []
    for access in opportunity_accesses:
        message = _get_inactive_message(access)
        if message:
            messages.append(message)
    send_message_bulk(messages)


def _get_inactive_message(access: OpportunityAccess):
    has_claimed_opportunity = OpportunityClaim.objects.filter(opportunity_access=access).exists()
    if has_claimed_opportunity:
        message = _check_deliver_inactive(access)
    else:
        # Send notification if user has completed learn modules and has not claimed the opportunity
        if access.learn_progress == 100:
            message = _get_deliver_message(access)
        else:
            message = _get_learn_message(access)
    return message


def _get_learn_message(access: OpportunityAccess):
    last_user_learn_module = (
        CompletedModule.objects.filter(user=access.user, opportunity=access.opportunity).order_by("date").last()
    )
    if last_user_learn_module and is_date_before(last_user_learn_module.date, days=3):
        return Message(
            usernames=[access.user.username],
            title=gettext(f"Resume your learning journey for {access.opportunity.name}"),
            body=gettext(
                f"You have not completed your learning for {access.opportunity.name}."
                "Please complete the learning modules to start delivering visits."
            ),
        )


def _check_deliver_inactive(access: OpportunityAccess):
    last_user_deliver_visit = (
        UserVisit.objects.filter(user=access.user, opportunity=access.opportunity).order_by("visit_date").last()
    )
    if last_user_deliver_visit and is_date_before(last_user_deliver_visit.visit_date, days=2):
        return _get_deliver_message(access)


def _get_deliver_message(access: OpportunityAccess):
    return Message(
        usernames=[access.user.username],
        title=gettext(f"Resume your job for {access.opportunity.name}"),
        body=gettext(
            f"You have not completed your delivery visits for {access.opportunity.name}."
            "To maximise your payout complete all the required service delivery."
        ),
    )
