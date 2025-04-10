import datetime
import logging

import httpx
from allauth.utils import build_absolute_uri
from django.conf import settings
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import transaction
from django.urls import reverse
from django.utils.timezone import now
from django.utils.translation import gettext
from tablib import Dataset

from commcare_connect.connect_id_client import fetch_users, filter_users, send_message, send_message_bulk
from commcare_connect.connect_id_client.models import ConnectIdUser, Message
from commcare_connect.opportunity.app_xml import get_connect_blocks_for_app, get_deliver_units_for_app
from commcare_connect.opportunity.export import (
    export_catchment_area_table,
    export_deliver_status_table,
    export_empty_payment_table,
    export_user_status_table,
    export_user_visit_data,
    export_user_visit_review_data,
    export_work_status_table,
)
from commcare_connect.opportunity.forms import DateRanges
from commcare_connect.opportunity.models import (
    BlobMeta,
    CompletedWorkStatus,
    DeliverUnit,
    LearnModule,
    Opportunity,
    OpportunityAccess,
    OpportunityClaim,
    Payment,
    UserInvite,
    UserInviteStatus,
    UserVisit,
    VisitReviewStatus,
    VisitValidationStatus,
)
from commcare_connect.opportunity.utils.completed_work import update_status
from commcare_connect.users.models import User
from commcare_connect.utils.datetime import is_date_before
from commcare_connect.utils.sms import send_sms
from config import celery_app

logger = logging.getLogger(__name__)


@celery_app.task()
def create_learn_modules_and_deliver_units(opportunity_id):
    opportunity = Opportunity.objects.get(id=opportunity_id)
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
        DeliverUnit.objects.get_or_create(app=deliver_app, slug=block.id, defaults=dict(name=block.name))


@celery_app.task()
def add_connect_users(
    user_list: list[str], opportunity_id: str, filter_country: str = "", filter_credential: list[str] = ""
):
    found_users = fetch_users(user_list)
    if filter_country or filter_credential:
        found_users += filter_users(country_code=filter_country, credential=filter_credential)
    not_found_users = set(user_list) - {user.phone_number for user in found_users}
    for u in not_found_users:
        UserInvite.objects.get_or_create(
            opportunity_id=opportunity_id,
            phone_number=u,
            status=UserInviteStatus.not_found,
        )
    for user in found_users:
        update_user_and_send_invite(user, opportunity_id)


def update_user_and_send_invite(user: ConnectIdUser, opp_id):
    u, _ = User.objects.update_or_create(
        username=user.username, defaults={"phone_number": user.phone_number, "name": user.name}
    )
    opportunity_access, _ = OpportunityAccess.objects.get_or_create(user=u, opportunity_id=opp_id)
    UserInvite.objects.update_or_create(
        opportunity_id=opp_id,
        phone_number=user.phone_number,
        defaults={"opportunity_access": opportunity_access},
    )
    invite_user.delay(u.pk, opportunity_access.pk)


@celery_app.task()
def invite_user(user_id, opportunity_access_id):
    user = User.objects.get(pk=user_id)
    opportunity_access = OpportunityAccess.objects.get(pk=opportunity_access_id)
    invite_id = opportunity_access.invite_id
    location = reverse("users:accept_invite", args=(invite_id,))
    url = build_absolute_uri(None, location)
    body = (
        "You have been invited to a new job in Commcare Connect. Click the following "
        f"link to share your information with the project and find out more {url}"
    )
    if not user.phone_number:
        return
    sms_status = send_sms(user.phone_number, body)
    UserInvite.objects.update_or_create(
        opportunity_access=opportunity_access,
        defaults={
            "message_sid": sms_status.sid,
            "status": UserInviteStatus.accepted if opportunity_access.accepted else UserInviteStatus.invited,
        },
    )
    message = Message(
        usernames=[user.username],
        data={
            "action": "ccc_opportunity_summary_page",
            "opportunity_id": str(opportunity_access.opportunity.id),
            "title": gettext(
                f"You have been invited to a CommCare Connect opportunity - {opportunity_access.opportunity.name}"
            ),
            "body": gettext(
                f"You have been invited to a new job in Commcare Connect - {opportunity_access.opportunity.name}"
            ),
        },
    )
    send_message(message)


@celery_app.task()
def generate_visit_export(opportunity_id: int, date_range: str, status: list[str], export_format: str, flatten: bool):
    opportunity = Opportunity.objects.get(id=opportunity_id)
    logger.info(f"Export for {opportunity.name} with date range {date_range} and status {','.join(status)}")
    dataset = export_user_visit_data(
        opportunity, DateRanges(date_range), [VisitValidationStatus(s) for s in status], flatten
    )
    export_tmp_name = f"{now().isoformat()}_{opportunity.name}_visit_export.{export_format}"
    save_export(dataset, export_tmp_name, export_format)
    return export_tmp_name


@celery_app.task()
def generate_review_visit_export(opportunity_id: int, date_range: str, status: list[str], export_format: str):
    opportunity = Opportunity.objects.get(id=opportunity_id)
    logger.info(
        f"Export review visit for {opportunity.name} with date range {date_range} and status {','.join(status)}"
    )
    dataset = export_user_visit_review_data(
        opportunity, DateRanges(date_range), [VisitReviewStatus(s) for s in status]
    )
    export_tmp_name = f"{now().isoformat()}_{opportunity.name}_review_visit_export.{export_format}"
    save_export(dataset, export_tmp_name, export_format)
    return export_tmp_name


@celery_app.task()
def generate_payment_export(opportunity_id: int, export_format: str):
    opportunity = Opportunity.objects.get(id=opportunity_id)
    dataset = export_empty_payment_table(opportunity)
    export_tmp_name = f"{now().isoformat()}_{opportunity.name}_payment_export.{export_format}"
    save_export(dataset, export_tmp_name, export_format)
    return export_tmp_name


@celery_app.task()
def generate_user_status_export(opportunity_id: int, export_format: str):
    opportunity = Opportunity.objects.get(id=opportunity_id)
    dataset = export_user_status_table(opportunity)
    export_tmp_name = f"{now().isoformat()}_{opportunity.name}_user_status.{export_format}"
    save_export(dataset, export_tmp_name, export_format)
    return export_tmp_name


@celery_app.task()
def generate_deliver_status_export(opportunity_id: int, export_format: str):
    opportunity = Opportunity.objects.get(id=opportunity_id)
    dataset = export_deliver_status_table(opportunity)
    export_tmp_name = f"{now().isoformat()}_{opportunity.name}_deliver_status.{export_format}"
    save_export(dataset, export_tmp_name, export_format)
    return export_tmp_name


def save_export(dataset: Dataset, file_name: str, export_format: str):
    content = dataset.export(export_format)
    if isinstance(content, str):
        content = content.encode()
    default_storage.save(file_name, ContentFile(content))


@celery_app.task()
def send_notification_inactive_users():
    opportunity_accesses = OpportunityAccess.objects.filter(
        opportunity__active=True,
        opportunity__end_date__gte=datetime.date.today(),
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
    last_user_learn_module = access.completedmodule_set.order_by("date").last()
    if last_user_learn_module and is_date_before(last_user_learn_module.date, days=3):
        return Message(
            usernames=[access.user.username],
            data={
                "action": "ccc_learn_progress",
                "opportunity_id": str(access.opportunity.id),
                "title": gettext(f"Resume your learning journey for {access.opportunity.name}"),
                "body": gettext(
                    f"You have not completed your learning for {access.opportunity.name}."
                    "Please complete the learning modules to start delivering visits."
                ),
            },
        )


def _check_deliver_inactive(access: OpportunityAccess):
    last_user_deliver_visit = access.uservisit_set.order_by("visit_date").last()
    if last_user_deliver_visit and is_date_before(last_user_deliver_visit.visit_date, days=2):
        return _get_deliver_message(access)


def _get_deliver_message(access: OpportunityAccess):
    return Message(
        usernames=[access.user.username],
        data={
            "action": "ccc_delivery_progress",
            "opportunity_id": str(access.opportunity.id),
            "title": gettext(f"Resume your job for {access.opportunity.name}"),
            "body": gettext(
                f"You have not completed your delivery visits for {access.opportunity.name}."
                "To maximise your payout complete all the required service delivery."
            ),
        },
    )


@celery_app.task()
def send_payment_notification(opportunity_id: int, payment_ids: list[int]):
    opportunity = Opportunity.objects.get(pk=opportunity_id)
    messages = []
    for payment in Payment.objects.filter(pk__in=payment_ids).select_related("opportunity_access__user"):
        messages.append(
            Message(
                usernames=[payment.opportunity_access.user.username],
                data={
                    "action": "ccc_payment",
                    "opportunity_id": str(opportunity.id),
                    "title": gettext("Payment received"),
                    "body": gettext(
                        "You have received a payment of"
                        f"{opportunity.currency} {payment.amount} for {opportunity.name}.",
                    ),
                    "payment_id": str(payment.id),
                },
            )
        )
    send_message_bulk(messages)


@celery_app.task()
def send_push_notification_task(user_ids: list[int], title: str, body: str):
    usernames = list(User.objects.filter(id__in=user_ids).values_list("username", flat=True))
    message = Message(usernames, title=title, body=body)
    send_message(message)


@celery_app.task()
def send_sms_task(user_ids: list[int], body: str):
    user_phone_numbers = User.objects.filter(id__in=user_ids).values_list("phone_number", flat=True)
    for phone_number in user_phone_numbers:
        send_sms(phone_number, body)


@celery_app.task()
def download_user_visit_attachments(user_visit_id: id):
    user_visit = UserVisit.objects.get(id=user_visit_id)
    api_key = user_visit.opportunity.api_key
    blobs = user_visit.form_json.get("attachments", {})
    domain = user_visit.opportunity.deliver_app.cc_domain
    form_id = user_visit.xform_id
    for name, blob in blobs.items():
        if name == "form.xml":
            continue
        url = f"{settings.COMMCARE_HQ_URL}/a/{domain}/api/form/attachment/{user_visit.xform_id}/{name}"

        with transaction.atomic():
            blob_meta, created = BlobMeta.objects.get_or_create(
                name=name, parent_id=form_id, content_length=blob["length"], content_type=blob["content_type"]
            )
            if not created:
                # attachment already exists
                continue
            response = httpx.get(
                url,
                headers={"Authorization": f"ApiKey {api_key.user.email}:{api_key.api_key}"},
            )
            default_storage.save(str(blob_meta.blob_id), ContentFile(response.content, name))


@celery_app.task()
def generate_work_status_export(opportunity_id: int, export_format: str):
    opportunity = Opportunity.objects.get(id=opportunity_id)
    dataset = export_work_status_table(opportunity)
    export_tmp_name = f"{now().isoformat()}_{opportunity.name}_payment_verification.{export_format}"
    save_export(dataset, export_tmp_name, export_format)
    return export_tmp_name


@celery_app.task()
def bulk_approve_completed_work():
    access_objects = OpportunityAccess.objects.filter(
        opportunity__active=True,
        opportunity__end_date__gte=datetime.date.today(),
        opportunity__auto_approve_payments=True,
        suspended=False,
    )
    for access in access_objects:
        completed_works = access.completedwork_set.exclude(status=CompletedWorkStatus.rejected)
        update_status(completed_works, access, compute_payment=True)


@celery_app.task()
def generate_catchment_area_export(opportunity_id: int, export_format: str):
    opportunity = Opportunity.objects.get(id=opportunity_id)
    dataset = export_catchment_area_table(opportunity)
    export_tmp_name = f"{now().isoformat()}_{opportunity.name}_catchment_area.{export_format}"
    save_export(dataset, export_tmp_name, export_format)
    return export_tmp_name


@celery_app.task()
def bulk_update_payment_accrued(opportunity_id, user_ids: list):
    """Updates payment accrued for completed and approved CompletedWork instances."""
    access_objects = OpportunityAccess.objects.filter(opportunity=opportunity_id, user__in=user_ids, suspended=False)
    for access in access_objects:
        with cache.lock(f"update_payment_accrued_lock_{access.id}", timeout=900):
            completed_works = access.completedwork_set.exclude(status=CompletedWorkStatus.rejected).select_related(
                "payment_unit"
            )
            update_status(completed_works, access, compute_payment=True)
