import codecs
import mimetypes
import textwrap
from dataclasses import dataclass

from django.core.files.uploadedfile import UploadedFile
from django.db import transaction
from tablib import Dataset

from commcare_connect.opportunity.models import (
    CompletedWork,
    CompletedWorkStatus,
    Opportunity,
    OpportunityAccess,
    Payment,
    UserVisit,
    VisitValidationStatus,
)
from commcare_connect.opportunity.tasks import send_payment_notification
from commcare_connect.utils.itertools import batched

VISIT_ID_COL = "visit id"
STATUS_COL = "status"
USERNAME_COL = "username"
AMOUNT_COL = "payment amount"
REASON_COL = "rejected reason"
WORK_ID_COL = "instance id"
PAYMENT_APPROVAL_STATUS_COL = "payment approval"
REQUIRED_COLS = [VISIT_ID_COL, STATUS_COL]


class ImportException(Exception):
    def __init__(self, message, rows=None):
        self.message = message
        self.rows = rows


@dataclass
class VisitImportStatus:
    seen_visits: set[str]
    missing_visits: set[str]

    def __len__(self):
        return len(self.seen_visits)

    def get_missing_message(self):
        joined = ", ".join(self.missing_visits)
        missing = textwrap.wrap(joined, width=115, break_long_words=False, break_on_hyphens=False)
        return f"<br>{len(self.missing_visits)} visits were not found:<br>{'<br>'.join(missing)}"


@dataclass
class PaymentImportStatus:
    seen_users: set[str]
    missing_users: set[str]

    def __len__(self):
        return len(self.seen_users)

    def get_missing_message(self):
        joined = ", ".join(self.missing_users)
        missing = textwrap.wrap(joined, width=115, break_long_words=False, break_on_hyphens=False)
        return f"<br>{len(self.missing_users)} usernames were not found:<br>{'<br>'.join(missing)}"


@dataclass
class CompletedWorkImportStatus:
    seen_completed_works: set[str]
    missing_completed_works: set[str]

    def __len__(self):
        return len(self.seen_completed_works)

    def get_missing_message(self):
        joined = ", ".join(self.missing_completed_works)
        missing = textwrap.wrap(joined, width=115, break_long_words=False, break_on_hyphens=False)
        return f"<br>{len(self.missing_completed_works)} completed works were not found:<br>{'<br>'.join(missing)}"


def bulk_update_visit_status(opportunity: Opportunity, file: UploadedFile) -> VisitImportStatus:
    file_format = None
    if file.content_type:
        file_format = mimetypes.guess_extension(file.content_type)
        if file_format:
            file_format = file_format[1:]
    if not file_format:
        file_format = file.name.split(".")[-1].lower()
    if file_format not in ("csv", "xlsx"):
        raise ImportException(f"Invalid file format. Only 'CSV' and 'XLSX' are supported. Got {file_format}")
    imported_data = get_imported_dataset(file, file_format)
    return _bulk_update_visit_status(opportunity, imported_data)


def _bulk_update_visit_status(opportunity: Opportunity, dataset: Dataset):
    status_by_visit_id, reasons_by_visit_id = get_status_by_visit_id(dataset)
    visit_ids = list(status_by_visit_id)
    missing_visits = set()
    seen_visits = set()
    user_ids = set()
    seen_completed_works = set()
    with transaction.atomic():
        for visit_batch in batched(visit_ids, 100):
            to_update = []
            visits = UserVisit.objects.filter(xform_id__in=visit_batch, opportunity=opportunity)
            for visit in visits:
                seen_visits.add(visit.xform_id)
                seen_completed_works.add(visit.completed_work_id)
                status = status_by_visit_id[visit.xform_id]
                if visit.status != status:
                    visit.status = status
                    reason = reasons_by_visit_id.get(visit.xform_id)
                    if visit.status == VisitValidationStatus.rejected and reason:
                        visit.reason = reason
                    to_update.append(visit)
                user_ids.add(visit.user_id)

            UserVisit.objects.bulk_update(to_update, fields=["status", "reason"])
            missing_visits |= set(visit_batch) - seen_visits
    update_payment_accrued(opportunity, users=user_ids)

    return VisitImportStatus(seen_visits, missing_visits)


def update_payment_accrued(opportunity: Opportunity, users):
    """Updates payment accrued for completed and approved CompletedWork instances."""
    access_objects = OpportunityAccess.objects.filter(user__in=users, opportunity=opportunity, suspended=False)
    for access in access_objects:
        completed_works = access.completedwork_set.exclude(
            status__in=[CompletedWorkStatus.rejected, CompletedWorkStatus.over_limit]
        ).select_related("payment_unit")
        access.payment_accrued = 0
        for completed_work in completed_works:
            # Auto Approve Payment conditions
            if opportunity.auto_approve_payments:
                visits = completed_work.uservisit_set.values_list("status", "reason")
                if any(status == "rejected" for status, _ in visits):
                    completed_work.status = CompletedWorkStatus.rejected
                    completed_work.reason = "\n".join(reason for _, reason in visits if reason)
                elif all(status == "approved" for status, _ in visits):
                    completed_work.status = CompletedWorkStatus.approved
            approved_count = completed_work.approved_count
            if approved_count > 0 and completed_work.status == CompletedWorkStatus.approved:
                access.payment_accrued += approved_count * completed_work.payment_unit.amount
            completed_work.save()
        access.save()


def get_status_by_visit_id(dataset) -> dict[int, VisitValidationStatus]:
    headers = [header.lower() for header in dataset.headers or []]
    if not headers:
        raise ImportException("The uploaded file did not contain any headers")

    visit_col_index = _get_header_index(headers, VISIT_ID_COL)
    status_col_index = _get_header_index(headers, STATUS_COL)
    reason_col_index = _get_header_index(headers, REASON_COL)
    status_by_visit_id = {}
    reason_by_visit_id = {}
    invalid_rows = []
    for row in dataset:
        row = list(row)
        visit_id = str(row[visit_col_index])
        status_raw = row[status_col_index].lower().strip().replace(" ", "_")
        try:
            status_by_visit_id[visit_id] = VisitValidationStatus[status_raw]
        except KeyError:
            invalid_rows.append((row, f"status must be one of {VisitValidationStatus.values}"))
        if status_raw == VisitValidationStatus.rejected.value:
            reason_by_visit_id[visit_id] = str(row[reason_col_index])

    if invalid_rows:
        raise ImportException(f"{len(invalid_rows)} have errors", invalid_rows)
    return status_by_visit_id, reason_by_visit_id


def get_imported_dataset(file, file_format):
    if file_format == "csv":
        file = codecs.iterdecode(file, "utf-8")
    imported_data = Dataset().load(file, format=file_format)
    return imported_data


def _get_header_index(headers: list[str], col_name: str) -> int:
    try:
        return headers.index(col_name)
    except ValueError:
        raise ImportException(f"Missing required column(s): '{col_name}'")


def bulk_update_payment_status(opportunity: Opportunity, file: UploadedFile) -> PaymentImportStatus:
    file_format = None
    if file.content_type:
        file_format = mimetypes.guess_extension(file.content_type)
        if file_format:
            file_format = file_format[1:]
    if not file_format:
        file_format = file.name.split(".")[-1].lower()
    if file_format not in ("csv", "xlsx"):
        raise ImportException(f"Invalid file format. Only 'CSV' and 'XLSX' are supported. Got {file_format}")
    imported_data = get_imported_dataset(file, file_format)
    return _bulk_update_payments(opportunity, imported_data)


def _bulk_update_payments(opportunity: Opportunity, imported_data: Dataset) -> PaymentImportStatus:
    headers = [header.lower() for header in imported_data.headers or []]
    if not headers:
        raise ImportException("The uploaded file did not contain any headers")

    username_col_index = _get_header_index(headers, USERNAME_COL)
    amount_col_index = _get_header_index(headers, AMOUNT_COL)
    invalid_rows = []
    payments = {}
    for row in imported_data:
        row = list(row)
        username = str(row[username_col_index])
        amount_raw = row[amount_col_index]
        if amount_raw:
            if not username:
                invalid_rows.append((row, "username required"))
            try:
                amount = int(amount_raw)
            except ValueError:
                invalid_rows.append((row, "amount must be an integer"))
            payments[username] = amount

    if invalid_rows:
        raise ImportException(f"{len(invalid_rows)} have errors", invalid_rows)

    seen_users = set()
    payment_ids = []
    with transaction.atomic():
        usernames = list(payments)
        users = OpportunityAccess.objects.filter(
            user__username__in=usernames, opportunity=opportunity, suspended=False
        ).select_related("user")
        for access in users:
            username = access.user.username
            amount = payments[username]
            payment = Payment.objects.create(opportunity_access=access, amount=amount)
            seen_users.add(username)
            payment_ids.append(payment.pk)
    missing_users = set(usernames) - seen_users
    send_payment_notification.delay(opportunity.id, payment_ids)
    return PaymentImportStatus(seen_users, missing_users)


def bulk_update_completed_work_status(opportunity: Opportunity, file: UploadedFile) -> CompletedWorkImportStatus:
    file_format = None
    if file.content_type:
        file_format = mimetypes.guess_extension(file.content_type)
        if file_format:
            file_format = file_format[1:]
    if not file_format:
        file_format = file.name.split(".")[-1].lower()
    if file_format not in ("csv", "xlsx"):
        raise ImportException(f"Invalid file format. Only 'CSV' and 'XLSX' are supported. Got {file_format}")
    imported_data = get_imported_dataset(file, file_format)
    return _bulk_update_completed_work_status(opportunity, imported_data)


def _bulk_update_completed_work_status(opportunity: Opportunity, dataset: Dataset):
    status_by_work_id, reasons_by_work_id = get_status_by_completed_work_id(dataset)
    work_ids = list(status_by_work_id)
    missing_completed_works = set()
    seen_completed_works = set()
    user_ids = set()
    with transaction.atomic():
        for work_batch in batched(work_ids, 100):
            to_update = []
            completed_works = CompletedWork.objects.filter(
                id__in=work_batch, opportunity_access__opportunity=opportunity
            )
            for completed_work in completed_works:
                seen_completed_works.add(str(completed_work.id))
                status = status_by_work_id[str(completed_work.id)]
                if completed_work.status != status:
                    completed_work.status = status
                    reason = reasons_by_work_id.get(str(completed_work.id))
                    if completed_work.status == CompletedWorkStatus.rejected and reason:
                        completed_work.reason = reason
                    to_update.append(completed_work)
                user_ids.add(completed_work.opportunity_access.user_id)
            CompletedWork.objects.bulk_update(to_update, fields=["status", "reason"])
            missing_completed_works |= set(work_batch) - seen_completed_works
        update_payment_accrued(opportunity, users=user_ids)
    return CompletedWorkImportStatus(seen_completed_works, missing_completed_works)


def get_status_by_completed_work_id(dataset):
    headers = [header.lower() for header in dataset.headers or []]
    if not headers:
        raise ImportException("The uploaded file did not contain any headers")

    work_id_col_index = _get_header_index(headers, WORK_ID_COL)
    status_col_index = _get_header_index(headers, PAYMENT_APPROVAL_STATUS_COL)
    reason_col_index = _get_header_index(headers, REASON_COL)
    status_by_work_id = {}
    reason_by_work_id = {}
    invalid_rows = []
    for row in dataset:
        row = list(row)
        work_id = str(row[work_id_col_index])
        status_raw = row[status_col_index].lower().strip().replace(" ", "_")
        try:
            status_by_work_id[work_id] = CompletedWorkStatus[status_raw]
        except KeyError:
            invalid_rows.append((row, f"status must be one of {CompletedWorkStatus.values}"))
        if status_raw == CompletedWorkStatus.rejected.value:
            reason_by_work_id[work_id] = str(row[reason_col_index])

    if invalid_rows:
        raise ImportException(f"{len(invalid_rows)} have errors", invalid_rows)
    return status_by_work_id, reason_by_work_id
