import datetime
import secrets

from django.db.models import Min

from commcare_connect.opportunity.models import CompletedWork, CompletedWorkStatus


def get_start_date_for_invoice(opportunity):
    date = (
        CompletedWork.objects.filter(
            invoice__isnull=True,
            opportunity_access__opportunity=opportunity,
            status=CompletedWorkStatus.approved,
        )
        .aggregate(earliest_date=Min("status_modified_date"))
        .get("earliest_date")
    )

    if date:
        start_date = date.date()
    else:
        start_date = opportunity.start_date

    return start_date.replace(day=1)


def get_end_date_for_invoice(start_date):
    last_day_previous_month = datetime.date.today().replace(day=1) - datetime.timedelta(days=1)

    if start_date > last_day_previous_month:
        return datetime.date.today() - datetime.timedelta(days=1)
    return last_day_previous_month


def generate_invoice_number():
    return secrets.token_hex(5).upper()
