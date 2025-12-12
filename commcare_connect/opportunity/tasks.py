import datetime
import logging
from decimal import Decimal

import httpx
import sentry_sdk
import waffle
from allauth.utils import build_absolute_uri
from django.conf import settings
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.mail import send_mail
from django.db import transaction
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.timezone import now
from django.utils.translation import gettext
from tablib import Dataset

from commcare_connect.cache import quickcache
from commcare_connect.connect_id_client import fetch_users, send_message, send_message_bulk
from commcare_connect.connect_id_client.models import ConnectIdUser, Message
from commcare_connect.flags.switch_names import AUTOMATED_INVOICES_MONTHLY
from commcare_connect.opportunity.app_xml import get_connect_blocks_for_app, get_deliver_units_for_app
from commcare_connect.opportunity.export import (
    UserVisitExporter,
    export_catchment_area_table,
    export_deliver_status_table,
    export_empty_payment_table,
    export_user_status_table,
    export_user_visit_review_data,
    export_work_status_table,
)
from commcare_connect.opportunity.models import (
    BlobMeta,
    CompletedWorkStatus,
    DeliverUnit,
    ExchangeRate,
    InvoiceStatus,
    LearnModule,
    Opportunity,
    OpportunityAccess,
    OpportunityClaim,
    Payment,
    PaymentInvoice,
    UserInvite,
    UserInviteStatus,
    UserVisit,
    VisitReviewStatus,
    VisitValidationStatus,
)
from commcare_connect.opportunity.utils.completed_work import (
    get_uninvoiced_visit_items,
    link_invoice_to_completed_works,
    update_status,
)
from commcare_connect.opportunity.utils.invoice import generate_invoice_number, get_start_date_for_invoice
from commcare_connect.users.models import User
from commcare_connect.users.user_credentials import UserCredentialIssuer
from commcare_connect.utils.analytics import Event, GATrackingInfo, _serialize_events, send_event_task
from commcare_connect.utils.celery import set_task_progress
from commcare_connect.utils.datetime import get_end_date_previous_month, is_date_before
from commcare_connect.utils.sms import send_sms
from config import celery_app

logger = logging.getLogger(__name__)


@celery_app.task()
def create_learn_modules_and_deliver_units(opportunity_id):
    opportunity = Opportunity.objects.get(id=opportunity_id)
    learn_app = opportunity.learn_app
    deliver_app = opportunity.deliver_app
    learn_app_connect_blocks = get_connect_blocks_for_app(learn_app)
    deliver_app_connect_blocks = get_deliver_units_for_app(deliver_app)

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
def add_connect_users(user_list: list[str], opportunity_id: str):
    found_users = fetch_users(user_list)
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
    body = f"You have been invited to a job in Connect. Click the link to accept {url}"
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
def generate_visit_export(
    opportunity_id: int, from_date, to_date, status: list[str], export_format: str, flatten: bool
):
    opportunity = Opportunity.objects.get(id=opportunity_id)
    logger.info(
        f"Export for {opportunity.name} with date range from {from_date} to {to_date} and status {','.join(status)}"
    )
    exporter = UserVisitExporter(opportunity, flatten)
    dataset = exporter.get_dataset(from_date, to_date, [VisitValidationStatus(s) for s in status])
    export_tmp_name = f"{now().isoformat()}_{opportunity.name}_visit_export.{export_format}"
    save_export(dataset, export_tmp_name, export_format)
    return export_tmp_name


@celery_app.task()
def generate_review_visit_export(opportunity_id: int, from_date, to_date, status: list[str], export_format: str):
    opportunity = Opportunity.objects.get(id=opportunity_id)
    logger.info(
        f"""Export review visit for {opportunity.name} with date
        from {from_date} to {to_date} and status {','.join(status)}"""
    )
    dataset = export_user_visit_review_data(opportunity, from_date, to_date, [VisitReviewStatus(s) for s in status])
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
                    f"You have not completed your learning for {access.opportunity.name}. "
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
                f"You have not completed your delivery visits for {access.opportunity.name}. "
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
                        "You have received a payment of "
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
    data = {"title": title, "body": body}
    message = Message(usernames, data=data)
    send_message(message)


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
        url = f"{api_key.hq_server.url}/a/{domain}/api/form/attachment/{user_visit.xform_id}/{name}"

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


def get_payment_upload_key(opp_id):
    return f"bulk_update_payments_opportunity_{opp_id}"


@celery_app.task(bind=True)
def bulk_update_payments_task(self, opportunity_id: int, file_path: str, file_format: str):
    from commcare_connect.opportunity.visit_import import ImportException, bulk_update_payments, get_imported_dataset

    with cache.lock(get_payment_upload_key(opportunity_id), timeout=600):
        set_task_progress(self, "Payment Record Import is in progress.")
        try:
            with default_storage.open(file_path, "rb") as f:
                dataset = get_imported_dataset(f, file_format)
                headers = dataset.headers or []
                rows = list(dataset)

            status = bulk_update_payments(opportunity_id, headers, rows)
            messages = [f"Payment status updated successfully for {len(status)} users."]
            if status.missing_users:
                messages.append(status.get_missing_message())

        except ImportException as e:
            messages = [f"Payment Import failed: {e}"] + getattr(e, "invalid_rows", [])
        except Exception as e:
            messages = [f"Unexpected error during payment import: {e}"]
        finally:
            default_storage.delete(file_path)

        set_task_progress(self, "<br>".join(messages), is_complete=True)


@celery_app.task(bind=True)
def bulk_update_visit_status_task(
    self, opportunity_id: int, file_path: str, file_format: str, tracking_info: dict = None
):
    from commcare_connect.opportunity.visit_import import (
        ImportException,
        bulk_update_visit_status,
        get_imported_dataset,
    )

    set_task_progress(self, "Visit Verification Import is in porgress.")
    try:
        with default_storage.open(file_path, "rb") as f:
            dataset = get_imported_dataset(f, file_format)
            headers = dataset.headers or []
            rows = list(dataset)

        status = bulk_update_visit_status(opportunity_id, headers, rows)
        messages = [f"Visit status updated successfully for {len(status)} visits.<br>"]
        if status.missing_visits:
            messages.append(status.get_missing_message())
        if status.locked_visits:
            messages.append(status.get_locked_message())

        if tracking_info:
            tracking_info = GATrackingInfo.from_dict(tracking_info)
            events = [
                Event("visit_import_approved", {"updated": status.approved_count, "total": len(status.seen_visits)}),
                Event("visit_import_rejected", {"updated": status.rejected_count, "total": len(status.seen_visits)}),
            ]
            for event in events:
                event.add_tracking_info(tracking_info)
            send_event_task.delay(tracking_info.client_id, _serialize_events(events))
    except ImportException as e:
        messages = [f"Visit status import failed: {e}"] + getattr(e, "invalid_rows", [])

    set_task_progress(self, "<br>".join(messages), is_complete=True)


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


@quickcache(vary_on=["url"], timeout=60 * 60 * 24)
def request_rates(url):
    response = httpx.get(url)
    rates = response.json()["rates"]
    return rates


@celery_app.task()
def fetch_exchange_rates(date=None, currency=None):
    base_url = "https://openexchangerates.org/api"

    if date is None:
        # fetch for the first of the month
        date = datetime.date.today().replace(day=1)
    url = f"{base_url}/historical/{date.strftime('%Y-%m-%d')}.json"
    url = f"{url}?app_id={settings.OPEN_EXCHANGE_RATES_API_ID}"
    rates = request_rates(url)

    if currency is None:
        currencies = Opportunity.objects.values_list("currency", flat=True).distinct()
        for currency in currencies:
            rate = rates.get(currency)
            if rate is None:
                message = f"Invalid currency for opportunity: {currency}"
                sentry_sdk.capture_message(message=message, level="error")
                continue
            ExchangeRate.objects.create(currency_code=currency, rate=rate, rate_date=date)
    else:
        # Parsing it to decimal otherwise the returned object rate will still be in float.
        rate = Decimal(rates[currency])
        return ExchangeRate.objects.create(currency_code=currency, rate=rate, rate_date=date)


@celery_app.task()
def issue_user_credentials():
    """Runs daily to issue credentials to users who have earned them."""
    UserCredentialIssuer.run()


@celery_app.task()
def submit_credentials_to_personalid_task():
    UserCredentialIssuer.submit_user_credentials()


@celery_app.task()
def send_invoice_paid_mail(opportunity_id, invoice_ids):
    logger.info(f"Sending invoice paid notification for opportunity {opportunity_id} and invoices {invoice_ids}")
    opportunity = Opportunity.objects.select_related("organization").get(pk=opportunity_id)
    nm_org = opportunity.organization
    recipient_emails = nm_org.get_member_emails()
    if not recipient_emails:
        logger.info(f"No recipient emails found for organization {nm_org.slug}. Skipping invoice paid email.")
        return

    invoices = PaymentInvoice.objects.filter(id__in=invoice_ids)

    base_invoice_list_url = build_absolute_uri(
        None,
        reverse(
            "opportunity:invoice_list",
            kwargs={"org_slug": nm_org.slug, "opp_id": opportunity.id},
        ),
    )

    for invoice in invoices:
        subject = f"[{opportunity.name}] Invoice {invoice.invoice_number} Has Been Paid"
        invoice_url = f"{base_invoice_list_url}?highlight={invoice.invoice_number}"
        context = {
            "invoice": invoice,
            "invoice_url": invoice_url,
            "opportunity": opportunity,
        }

        text_body = render_to_string(
            "opportunity/email/invoice_paid.txt",
            context,
        )
        html_body = render_to_string(
            "opportunity/email/invoice_paid.html",
            context,
        )
        send_mail(
            subject=subject,
            message=text_body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=recipient_emails,
            html_message=html_body,
        )


@celery_app.task()
def generate_automated_service_delivery_invoice():
    if not waffle.switch_is_active(AUTOMATED_INVOICES_MONTHLY):
        return

    CHUNK_SIZE = 100
    invoices_chunk = []
    end_date_prev_month = get_end_date_previous_month()

    for opportunity in Opportunity.objects.filter(active=True, managed=True).iterator(chunk_size=CHUNK_SIZE):
        start_date = get_start_date_for_invoice(opportunity)
        # Below indicates there are no uninvoiced completed works to invoice in previous month or earlier
        if start_date > end_date_prev_month:
            continue

        invoice_number = generate_invoice_number()
        line_items = get_uninvoiced_visit_items(opportunity, start_date, end_date_prev_month)
        if not line_items:
            continue

        total_local_amount = sum(item["total_amount_local"] for item in line_items)
        total_usd_amount = sum(item["total_amount_usd"] for item in line_items)

        payment_invoice = PaymentInvoice(
            opportunity=opportunity,
            amount=total_local_amount,
            amount_usd=total_usd_amount,
            date=datetime.datetime.utcnow(),
            start_date=start_date,
            end_date=end_date_prev_month,
            status=InvoiceStatus.PENDING,
            invoice_number=invoice_number,
            service_delivery=True,
            exchange_rate=line_items[-1].exchange_rate,
        )
        invoices_chunk.append(payment_invoice)

        if len(invoices_chunk) == CHUNK_SIZE:
            _bulk_create_and_link_invoices(invoices_chunk)
            invoices_chunk = []

    if invoices_chunk:
        _bulk_create_and_link_invoices(invoices_chunk)


def _bulk_create_and_link_invoices(invoices_chunk):
    with transaction.atomic():
        PaymentInvoice.objects.bulk_create(invoices_chunk)
        for invoice in invoices_chunk:
            link_invoice_to_completed_works(invoice, start_date=invoice.start_date, end_date=invoice.end_date)
