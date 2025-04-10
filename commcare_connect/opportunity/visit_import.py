import codecs
import datetime
import json
import textwrap
import urllib
from dataclasses import astuple, dataclass
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.cache import cache
from django.core.files.uploadedfile import UploadedFile
from django.db import transaction
from django.utils.timezone import now
from tablib import Dataset

from commcare_connect.cache import quickcache
from commcare_connect.opportunity.models import (
    CatchmentArea,
    CompletedWork,
    CompletedWorkStatus,
    ExchangeRate,
    Opportunity,
    OpportunityAccess,
    Payment,
    UserVisit,
    VisitReviewStatus,
    VisitValidationStatus,
)
from commcare_connect.opportunity.tasks import bulk_update_payment_accrued, send_payment_notification
from commcare_connect.opportunity.utils.completed_work import update_status, update_work_payment_date
from commcare_connect.utils.file import get_file_extension
from commcare_connect.utils.itertools import batched

VISIT_ID_COL = "visit id"
STATUS_COL = "status"
USERNAME_COL = "username"
AMOUNT_COL = "payment amount"
PAYMENT_DATE_COL = "payment date (yyyy-mm-dd)"
REASON_COL = "rejected reason"
JUSTIFICATION_COL = "justification"
WORK_ID_COL = "instance id"
PAYMENT_APPROVAL_STATUS_COL = "payment approval"
REQUIRED_COLS = [VISIT_ID_COL, STATUS_COL]
LATITUDE_COL = "latitude"
LONGITUDE_COL = "longitude"
RADIUS_COL = "radius"
AREA_NAME_COL = "area name"
ACTIVE_COL = "active"
SITE_CODE_COL = "site code"
PAYMENT_METHOD_COL = "payment method"
PAYMENT_OPERATOR_COL = "payment operator"
REVIEW_STATUS_COL = "program manager review"


class ImportException(Exception):
    def __init__(self, message, rows=None):
        self.message = message
        self.rows = rows


class RowDataError(Exception):
    pass


class InvalidValueError(RowDataError):
    pass


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


@dataclass
class CatchmentAreaImportStatus:
    seen_catchments: set[str]
    new_catchments: int

    def __len__(self):
        return len(self.seen_catchments)


@dataclass
class VisitData:
    status: VisitValidationStatus = VisitValidationStatus.pending
    reason: str = ""
    justification: str | None = ""

    def __iter__(self):
        return iter(astuple(self))


def bulk_update_visit_status(opportunity: Opportunity, file: UploadedFile) -> VisitImportStatus:
    file_format = get_file_extension(file)
    if file_format not in ("csv", "xlsx"):
        raise ImportException(f"Invalid file format. Only 'CSV' and 'XLSX' are supported. Got {file_format}")
    imported_data = get_imported_dataset(file, file_format)
    return _bulk_update_visit_status(opportunity, imported_data)


def _bulk_update_visit_status(opportunity: Opportunity, dataset: Dataset):
    data_by_visit_id = get_data_by_visit_id(dataset)
    visit_ids = list(data_by_visit_id.keys())
    missing_visits = set()
    seen_visits = set()
    user_ids = set()
    with transaction.atomic():
        missing_justifications = []
        for visit_batch in batched(visit_ids, 100):
            to_update = []
            visits = UserVisit.objects.filter(xform_id__in=visit_batch, opportunity=opportunity)
            for visit in visits:
                seen_visits.add(visit.xform_id)
                visit_data = data_by_visit_id[visit.xform_id]
                status, reason, justification = visit_data
                changed = False
                if visit.status != status:
                    visit.status = status
                    if opportunity.managed and status == VisitValidationStatus.approved:
                        visit.review_created_on = now()
                        if visit.flagged and not justification:
                            missing_justifications.append(visit.xform_id)
                            continue
                    changed = True
                if status == VisitValidationStatus.rejected and reason and reason != visit.reason:
                    visit.reason = reason
                    changed = True
                if justification and justification != visit.justification:
                    visit.justification = justification
                    changed = True

                if changed:
                    to_update.append(visit)
                user_ids.add(visit.user_id)

            if missing_justifications:
                raise ImportException(get_missing_justification_message(missing_justifications))

            UserVisit.objects.bulk_update(
                to_update, fields=["status", "reason", "review_created_on", "justification", "status_modified_date"]
            )
            missing_visits |= set(visit_batch) - seen_visits
    bulk_update_payment_accrued.delay(opportunity.id, list(user_ids))
    return VisitImportStatus(seen_visits, missing_visits)


def get_missing_justification_message(visits_ids):
    id_list = ", ".join(str(v_id) for v_id in visits_ids)
    return f"Justification is required for flagged visits: {id_list}"


def update_payment_accrued(opportunity: Opportunity, users):
    """Updates payment accrued for completed and approved CompletedWork instances."""
    access_objects = OpportunityAccess.objects.filter(user__in=users, opportunity=opportunity, suspended=False)
    for access in access_objects:
        with cache.lock(f"update_payment_accrued_lock_{access.id}", timeout=900):
            completed_works = access.completedwork_set.exclude(status=CompletedWorkStatus.rejected).select_related(
                "payment_unit"
            )
            update_status(completed_works, access, compute_payment=True)


def get_data_by_visit_id(dataset) -> dict[int, VisitData]:
    headers = [header.lower() for header in dataset.headers or []]
    if not headers:
        raise ImportException("The uploaded file did not contain any headers")

    visit_col_index = _get_header_index(headers, VISIT_ID_COL)
    status_col_index = _get_header_index(headers, STATUS_COL)
    reason_col_index = _get_header_index(headers, REASON_COL)
    justification_col_index = _get_header_index(headers, JUSTIFICATION_COL, required=False)
    data_by_visit_id = {}
    invalid_rows = []
    for row in dataset:
        row = list(row)
        visit_id = str(row[visit_col_index])
        status_raw = row[status_col_index].lower().strip().replace(" ", "_")
        visit_data = VisitData()
        try:
            visit_data.status = VisitValidationStatus[status_raw]
        except KeyError:
            invalid_rows.append((row, f"status must be one of {VisitValidationStatus.values}"))
        if status_raw == VisitValidationStatus.rejected.value:
            visit_data.reason = str(row[reason_col_index])
        if justification_col_index > 0:
            visit_data.justification = str(row[justification_col_index])
        data_by_visit_id[visit_id] = visit_data

    if invalid_rows:
        raise ImportException(f"{len(invalid_rows)} have errors", invalid_rows)
    return data_by_visit_id


def get_imported_dataset(file, file_format):
    if file_format == "csv":
        file = codecs.iterdecode(file, "utf-8")
    imported_data = Dataset().load(file, format=file_format)
    return imported_data


def _get_header_index(headers: list[str], col_name: str, required=True) -> int:
    try:
        return headers.index(col_name)
    except ValueError:
        if not required:
            return -1
        raise ImportException(f"Missing required column(s): '{col_name}'")


def bulk_update_payment_status(opportunity: Opportunity, file: UploadedFile) -> PaymentImportStatus:
    file_format = get_file_extension(file)
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
    payment_date_col_index = _get_header_index(headers, PAYMENT_DATE_COL)
    payment_method_col_index = _get_header_index(headers, PAYMENT_METHOD_COL)
    payment_operator_col_index = _get_header_index(headers, PAYMENT_OPERATOR_COL)
    invalid_rows = []
    payments = {}
    exchange_rate = get_exchange_rate(opportunity.currency)
    if not exchange_rate:
        raise ImportException(f"Currency code {opportunity.currency} is invalid")
    for row in imported_data:
        row = list(row)
        username = str(row[username_col_index])
        amount_raw = row[amount_col_index]
        payment_date_raw = row[payment_date_col_index]
        payment_method = row[payment_method_col_index]
        payment_operator = row[payment_operator_col_index]
        if not amount_raw:
            continue
        if not username:
            invalid_rows.append((row, "username required"))
        try:
            amount = int(amount_raw)
        except ValueError:
            invalid_rows.append((row, "amount must be an integer"))
        else:
            payments[username] = {"amount": amount}
        try:
            if payment_date_raw:
                if isinstance(payment_date_raw, datetime.datetime):
                    # Dataset autoparses valid datetime
                    payment_date = payment_date_raw
                else:
                    payment_date = datetime.datetime.strptime(payment_date_raw, "%Y-%m-%d").date()
            else:
                payment_date = None
        except ValueError:
            invalid_rows.append((row, "Payment Date must be in YYYY-MM-DD format"))
        else:
            payments[username]["payment_date"] = payment_date
        payments[username]["payment_method"] = payment_method
        payments[username]["payment_operator"] = payment_operator

    if invalid_rows:
        raise ImportException(f"{len(invalid_rows)} have errors", invalid_rows)

    seen_users = set()
    payment_ids = []
    lock_key = f"bulk_update_payments_opportunity_{opportunity.id}"
    with cache.lock(lock_key, timeout=600):
        with transaction.atomic():
            usernames = list(payments)
            users = OpportunityAccess.objects.filter(
                user__username__in=usernames, opportunity=opportunity, suspended=False
            ).select_related("user")
            for access in users:
                username = access.user.username
                amount = payments[username]["amount"]
                payment_date = payments[username]["payment_date"]
                payment_method = payments[username]["payment_method"]
                payment_operator = payments[username]["payment_operator"]
                payment_data = {
                    "opportunity_access": access,
                    "amount": amount,
                    "amount_usd": amount / exchange_rate,
                    "payment_method": payment_method,
                    "payment_operator": payment_operator,
                }
                if payment_date:
                    payment_data["date_paid"] = payment_date
                payment = Payment.objects.create(**payment_data)
                seen_users.add(username)
                payment_ids.append(payment.pk)
                update_work_payment_date(access)
    missing_users = set(usernames) - seen_users
    send_payment_notification.delay(opportunity.id, payment_ids)
    return PaymentImportStatus(seen_users, missing_users)


def _cache_key(date=None):
    date_key = date or now().date()
    return [date_key.toordinal()]


@quickcache(vary_on=_cache_key, timeout=12 * 60 * 60)
def fetch_exchange_rates(date=None):
    base_url = "https://openexchangerates.org/api"

    if date:
        url = f"{base_url}/historical/{date.strftime('%Y-%m-%d')}.json"
    else:
        url = f"{base_url}/latest.json"

    url = f"{url}?app_id={settings.OPEN_EXCHANGE_RATES_API_ID}"
    rates = json.load(urllib.request.urlopen(url))
    return rates["rates"]


def get_exchange_rate(currency_code, date=None):
    # date should be a date object or None for latest rate

    if currency_code is None:
        raise ImportException("Opportunity must have specified currency to import payments")

    currency_code = currency_code.upper()

    if currency_code == "USD":
        return 1

    rate_date = date or now().date()
    rate = None

    try:
        rate = ExchangeRate.objects.get(currency_code=currency_code, rate_date=rate_date).rate
    except ExchangeRate.DoesNotExist:
        rates = fetch_exchange_rates(rate_date)
        rate = rates.get(currency_code)
        if not rate:
            raise ImportException("Rate not found for opportunity currency")
        ExchangeRate.objects.create(currency_code=currency_code, rate=rate, rate_date=rate_date)

    return rate


def bulk_update_completed_work_status(opportunity: Opportunity, file: UploadedFile) -> CompletedWorkImportStatus:
    file_format = get_file_extension(file)
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
                reason = reasons_by_work_id.get(str(completed_work.id))
                changed = False

                if completed_work.status != status:
                    completed_work.status = status
                    changed = True
                if status == CompletedWorkStatus.rejected and reason and reason != completed_work.reason:
                    completed_work.reason = reason
                    changed = True

                if changed:
                    to_update.append(completed_work)
                user_ids.add(completed_work.opportunity_access.user_id)
            CompletedWork.objects.bulk_update(to_update, fields=["status", "reason", "status_modified_date"])
            missing_completed_works |= set(work_batch) - seen_completed_works

        bulk_update_payment_accrued.delay(opportunity.id, list(user_ids))
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


def bulk_update_catchments(opportunity: Opportunity, file: UploadedFile):
    file_format = get_file_extension(file)
    if file_format not in ("csv", "xlsx"):
        raise ImportException(f"Invalid file format. Only 'CSV' and 'XLSX' are supported. Got {file_format}")
    imported_data = get_imported_dataset(file, file_format)
    return _bulk_update_catchments(opportunity, imported_data)


class RowData:
    def __init__(self, row: list[str], headers: list[str]):
        self.row = row
        self.headers = headers
        self.latitude = self._get_latitude()
        self.longitude = self._get_longitude()
        self.radius = self._get_radius()
        self.active = self._get_active()
        self.area_name = self._get_area_name()
        self.username = self._get_username()
        self.site_code = self._get_site_code()

    def _get_latitude(self) -> Decimal:
        error_message = "Latitude must be between -90 and 90 degrees."
        index = _get_header_index(self.headers, LATITUDE_COL)
        try:
            latitude = Decimal(self.row[index])
        except (ValueError, TypeError, InvalidOperation):
            raise InvalidValueError(error_message)
        if not Decimal("-90") <= latitude <= Decimal("90"):
            raise InvalidValueError(error_message)
        return latitude

    def _get_longitude(self) -> Decimal:
        index = _get_header_index(self.headers, LONGITUDE_COL)
        try:
            longitude = Decimal(self.row[index])
        except (ValueError, TypeError, InvalidOperation):
            raise InvalidValueError(f"Invalid longitude value: {self.row[index]}")
        if not Decimal("-180") <= longitude <= Decimal("180"):
            raise InvalidValueError("Longitude must be between -180 and 180 degrees")
        return longitude

    def _get_radius(self) -> int:
        index = _get_header_index(self.headers, RADIUS_COL)
        try:
            radius = int(self.row[index])
        except (ValueError, TypeError):
            raise InvalidValueError(f"Invalid radius value: {self.row[index]}")
        if radius <= 0:
            raise InvalidValueError("Radius must be a positive integer")
        return radius

    def _get_active(self) -> bool:
        error_message = "Active status must be 'yes' or 'no'"
        index = _get_header_index(self.headers, ACTIVE_COL)
        active = self.row[index]
        if not active:
            raise InvalidValueError(error_message)
        active = active.lower().strip()
        if active not in ["yes", "no"]:
            raise InvalidValueError(error_message)
        return active == "yes"

    def _get_area_name(self) -> str:
        index = _get_header_index(self.headers, AREA_NAME_COL)
        area_name = self.row[index]
        if not area_name:
            raise InvalidValueError("Area name is not valid.")
        return area_name

    def _get_username(self) -> str | None:
        try:
            index = _get_header_index(self.headers, USERNAME_COL)
        except ImportException:
            return None
        username = self.row[index]
        return username if username else None

    def _get_site_code(self) -> str:
        index = _get_header_index(self.headers, SITE_CODE_COL)
        site_code = self.row[index]
        if not site_code:
            raise InvalidValueError("Site code is not provided.")
        return site_code


def create_or_update_catchment(row_data: RowData, opportunity: Opportunity, username_to_oa_map: dict):
    try:
        catchment = CatchmentArea.objects.get(site_code=row_data.site_code, opportunity=opportunity)
        catchment.latitude = row_data.latitude
        catchment.longitude = row_data.longitude
        catchment.radius = row_data.radius
        catchment.name = row_data.area_name
        catchment.active = row_data.active
        catchment.opportunity_access = username_to_oa_map.get(row_data.username, None)
        return catchment, False
    except CatchmentArea.DoesNotExist:
        return (
            CatchmentArea(
                latitude=row_data.latitude,
                longitude=row_data.longitude,
                radius=row_data.radius,
                opportunity=opportunity,
                name=row_data.area_name,
                active=row_data.active,
                site_code=row_data.site_code,
                opportunity_access=username_to_oa_map.get(row_data.username, None),
            ),
            True,
        )


def _bulk_update_catchments(opportunity: Opportunity, dataset: Dataset):
    headers = [header.lower() for header in dataset.headers or [] if header]
    if not headers:
        raise ImportException("The uploaded file did not contain any headers")

    with transaction.atomic():
        to_create = []
        to_update = []
        invalid_rows = []
        seen_catchments = set()
        new_catchments = 0

        username_to_oa_map = {}
        if USERNAME_COL in headers:
            username_index = _get_header_index(headers, USERNAME_COL)
            usernames = []
            for row in dataset:
                row_list = list(row)
                if row_list[username_index]:
                    usernames.append(row_list[username_index])

            opportunity_accesses = OpportunityAccess.objects.filter(
                opportunity=opportunity, user__username__in=usernames
            ).select_related("user")
            username_to_oa_map = {oa.user.username: oa for oa in opportunity_accesses}

        for row in dataset:
            row = list(row)
            try:
                row_data = RowData(row, headers)
                catchment, created = create_or_update_catchment(row_data, opportunity, username_to_oa_map)

                if created:
                    to_create.append(catchment)
                    new_catchments += 1
                else:
                    to_update.append(catchment)
                    seen_catchments.add(str(catchment.id))

            except InvalidValueError as e:
                invalid_rows.append((row, f"Error in row {row}: {e}"))

        if to_create:
            CatchmentArea.objects.bulk_create(to_create)

        if to_update:
            CatchmentArea.objects.bulk_update(
                to_update,
                [
                    "latitude",
                    "longitude",
                    "radius",
                    "active",
                    "name",
                    "opportunity_access",
                ],
            )

        if invalid_rows:
            raise ImportException(f"{len(invalid_rows)} rows have errors", invalid_rows)

    return CatchmentAreaImportStatus(seen_catchments, new_catchments)


class ReviewVisitRowData:
    def __init__(self, row_number: int, row: list[str], headers: list[str]):
        self.row = row
        self.row_number = row_number
        self.headers = headers
        self.visit_id = self._get_visit_id()
        self.review_status = self._get_review_status()

    def _get_visit_id(self):
        index = _get_header_index(self.headers, VISIT_ID_COL)
        visit_id = self.row[index].strip() if index < len(self.row) and self.row[index] else None

        if not visit_id:
            raise ImportException(f"Missing visit ID in the dataset at row {self.row_number}.")

        return visit_id

    def _get_review_status(self):
        index = _get_header_index(self.headers, REVIEW_STATUS_COL)
        status = self.row[index].strip() if index < len(self.row) and self.row[index] else None

        if not status:
            raise ImportException(f"Missing review status in the dataset at row {self.row_number}.")

        for choice, label in VisitReviewStatus.choices:
            if choice.lower() == status.lower() or label.lower() == status.lower():
                return choice

        raise ImportException(
            f"Invalid review status '{status}' at row {self.row_number}. Allowed values: {VisitReviewStatus.values}"
        )


def bulk_update_visit_review_status(opportunity: Opportunity, file: UploadedFile) -> VisitImportStatus:
    file_format = get_file_extension(file)
    if not opportunity.managed:
        raise ImportException("Action is only available for managed opportunity.")

    if file_format not in ("csv", "xlsx"):
        raise ImportException(f"Invalid file format. Only 'CSV' and 'XLSX' are supported. Got {file_format}")

    imported_data = get_imported_dataset(file, file_format)
    return _bulk_update_visit_review_status(opportunity, imported_data)


def _bulk_update_visit_review_status(opportunity: Opportunity, dataset: Dataset):
    headers = [header.lower() if header else header for header in dataset.headers or []]
    if not headers:
        raise ImportException("The uploaded file did not contain any headers")

    visit_data = {
        data.visit_id: data.review_status
        for row_number, row in enumerate(dataset, start=2)  # row 1 is of headers
        if any(row) and (data := ReviewVisitRowData(row_number, row, headers))
    }

    if not visit_data:
        return VisitImportStatus(set(), set())

    visit_ids = set(visit_data.keys())
    existing_visits = UserVisit.objects.filter(xform_id__in=visit_ids, review_created_on__isnull=False).only(
        "id", "xform_id", "review_status", "user_id"
    )

    to_update = []
    user_ids = set()
    updated_visit_ids = set()

    with transaction.atomic():
        for visit in existing_visits:
            new_status = visit_data.get(visit.xform_id)
            if new_status and visit.review_status != new_status:
                visit.review_status = new_status
                to_update.append(visit)
                updated_visit_ids.add(visit.xform_id)
                user_ids.add(visit.user_id)

        if to_update:
            UserVisit.objects.bulk_update(to_update, fields=["review_status"])

    if user_ids:
        bulk_update_payment_accrued.delay(opportunity.id, list(user_ids))

    missing_visits = visit_ids - {visit.xform_id for visit in existing_visits}

    return VisitImportStatus(updated_visit_ids, missing_visits)
