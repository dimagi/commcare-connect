import datetime
import json

from django.db.models import Sum
from django.utils.encoding import force_str
from flatten_dict import flatten
from tablib import Dataset

from commcare_connect.opportunity.forms import DateRanges
from commcare_connect.opportunity.helpers import (
    get_annotated_opportunity_access,
    get_annotated_opportunity_access_deliver_status,
)
from commcare_connect.opportunity.models import (
    CatchmentArea,
    CompletedWork,
    Opportunity,
    OpportunityAccess,
    UserVisit,
    VisitReviewStatus,
    VisitValidationStatus,
)
from commcare_connect.opportunity.tables import (
    CatchmentAreaTable,
    CompletedWorkTable,
    DeliverStatusTable,
    UserStatusTable,
    UserVisitReviewTable,
    UserVisitTable,
)


def export_user_visit_data(
    opportunity: Opportunity, date_range: DateRanges, status: list[VisitValidationStatus], flatten: bool
) -> Dataset:
    """Export all user visits for an opportunity."""
    user_visits = UserVisit.objects.filter(opportunity=opportunity)
    if date_range.get_cutoff_date():
        user_visits = user_visits.filter(visit_date__gte=date_range.get_cutoff_date())
    if status and "all" not in status:
        user_visits = user_visits.filter(status__in=status)
    user_visits = user_visits.order_by("visit_date")

    table = UserVisitTable(user_visits)
    exclude_columns = ("visit_date", "form_json", "details", "justification", "review_status")
    if opportunity.managed:
        exclude_columns = ("visit_date", "form_json", "details", "review_status")
    columns = [
        column
        for column in table.columns.iterall()
        if not (column.column.exclude_from_export or column.name in exclude_columns)
    ]
    base_data = [
        # form_json must be the last column in the row
        [row.get_cell_value(column.name) for column in columns] + [row.get_cell_value("form_json")]
        for row in table.rows
    ]
    base_headers = [force_str(column.header, strings_only=True) for column in columns]
    if flatten:
        return get_flattened_dataset(base_headers, base_data)
    else:
        base_headers.append("form_json")
        dataset = Dataset(title="Export", headers=base_headers)
        for row in base_data:
            form_json = json.dumps(row.pop())
            row.append(form_json)
            row_data = [force_str(col, strings_only=True) for col in row]
            dataset.append(row_data)
        return dataset


def export_user_visit_review_data(
    opportunity: Opportunity, date_range: DateRanges, status: list[VisitReviewStatus]
) -> Dataset:
    user_visits = UserVisit.objects.filter(opportunity=opportunity, review_created_on__isnull=False)
    if date_range.get_cutoff_date():
        user_visits = user_visits.filter(review_created_on__gte=date_range.get_cutoff_date())
    if status and "all" not in status:
        user_visits = user_visits.filter(review_status__in=status)
    user_visits = user_visits.order_by("visit_date")
    table = UserVisitReviewTable(user_visits)
    exclude_columns = {"pk", "details", "user_visit"}

    columns = []
    headers = []
    for column in table.columns.iterall():
        if not (column.column.exclude_from_export or column.name in exclude_columns):
            columns.append(column)
            headers.append(force_str(column.header, strings_only=True))

    dataset = append_row_data(Dataset(title="Export Review User Visit", headers=headers), table=table, columns=columns)
    return dataset


def get_flattened_dataset(headers: list[str], data: list[list]) -> Dataset:
    """Flatten the form json and add it to the dataset.

    :param headers: The headers for the dataset.
    :param data: The data for the dataset. It is expected that the last column in each row
        is the form JSON data.
    """
    schema = set()
    flat_data = []
    for row in data:
        form_json = row.pop()
        form_json.pop("attachments", None)
        flat_json = flatten(form_json, reducer="dot", enumerate_types=(list,))
        flat_data.append(flat_json)
        schema.update(flat_json.keys())

    schema = sorted(schema, key=_schema_sort)
    headers = headers + schema
    dataset = Dataset(title="Export", headers=headers)

    for row, flat_json in zip(data, flat_data):
        row.extend(flat_json.get(key, "") for key in schema)
        dataset.append([force_str(col, strings_only=True) for col in row])

    return dataset


def _schema_sort(item):
    return len(item.split(".")), item


def export_empty_payment_table(opportunity: Opportunity) -> Dataset:
    headers = [
        "Username",
        "Phone Number",
        "Name",
        "Payment Amount",
        "Payment Date (YYYY-MM-DD)",
        "Payment Method",
        "Payment Operator",
    ]
    dataset = Dataset(title="Export", headers=headers)

    access_objects = (
        OpportunityAccess.objects.filter(opportunity=opportunity, suspended=False)
        .select_related("user")
        .annotate(total_payments=Sum("payment__amount"))
    )

    for access in access_objects:
        row = (
            access.user.username,
            access.user.phone_number,
            access.user.name,
            "",
            "",
            "",
            "",
        )
        dataset.append(row)
    return dataset


def export_user_status_table(opportunity: Opportunity) -> Dataset:
    access_objects = get_annotated_opportunity_access(opportunity)
    table = UserStatusTable(access_objects, exclude=("date_popup", "view_profile"))
    return get_dataset(table, export_title="User status export")


def export_deliver_status_table(opportunity: Opportunity) -> Dataset:
    access_objects = get_annotated_opportunity_access_deliver_status(opportunity)
    table = DeliverStatusTable(access_objects, exclude=("details", "date_popup"))
    return get_dataset(table, export_title="Deliver Status export")


def export_work_status_table(opportunity: Opportunity) -> Dataset:
    access_objects = OpportunityAccess.objects.filter(opportunity=opportunity, suspended=False)
    completed_works = []
    for completed_work in CompletedWork.objects.filter(opportunity_access__in=access_objects):
        completed = completed_work.completed
        if opportunity.auto_approve_payments and completed and completed_work.flags:
            completed_works.append(completed_work)
            continue
        if completed:
            completed_works.append(completed_work)
    table = CompletedWorkTable(completed_works, exclude=("date_popup"))
    return get_dataset(table, export_title="Payment Verification export")


def export_catchment_area_table(opportunity):
    catchment_areas = CatchmentArea.objects.filter(opportunity=opportunity)
    table = CatchmentAreaTable(catchment_areas)
    return get_dataset(table, export_title="Catchment Area Export")


def get_dataset(table, export_title):
    columns = [column for column in table.columns.iterall()]
    headers = [force_str(column.header, strings_only=True) for column in columns]
    dataset = append_row_data(Dataset(title=export_title, headers=headers), table, columns)
    return dataset


def append_row_data(dataset, table, columns):
    for row in table.rows:
        row_value = []
        for column in columns:
            col_value = row.get_cell_value(column.name)
            if isinstance(col_value, datetime.datetime):
                col_value = col_value.replace(tzinfo=None)
            row_value.append(col_value)
        dataset.append(row_value)
    return dataset
