from datetime import date

from django.contrib.auth.decorators import login_required, user_passes_test
from django.db.models import Sum
from django.shortcuts import render
from django.views.decorators.http import require_GET

from commcare_connect.opportunity.models import Payment, UserVisit, VisitValidationStatus

from .tables import AdminReportTable

ADMIN_REPORT_START = (2020, 1)


def _increment(quarter):
    year, q = quarter
    if q < 4:
        q += 1
    else:
        year += 1
        q = 1
    return (year, q)


def _get_quarters_since_start():
    today = date.today()
    current_quarter = (today.year, (today.month - 1) // 3 + 1)
    quarters = []
    q = ADMIN_REPORT_START
    while q <= current_quarter:
        quarters.append(q)
        q = _increment(q)
    return quarters


def _get_table_data_for_quarter(quarter):
    quarter_start = date(quarter[0], quarter[1] * 3, 1)
    next_quarter = _increment(quarter)
    quarter_end = date(next_quarter[0], next_quarter[1] * 3, 1)
    visit_data = UserVisit.objects.filter(
        # opportunity_access__opportunity__is_test=False,
        status=VisitValidationStatus.approved,
        visit_date__gte=quarter_start,
        visit_date__lt=quarter_end,
    ).values("user", "entity_id")

    user_set = set()
    beneficiary_set = set()
    service_count = 0
    for v in visit_data:
        user_set.add(v["user"])
        beneficiary_set.add(v["entity_id"])
        service_count += 1

    payment_data = Payment.objects.filter(
        # opportunity_access__opportunity__is_test=False,
        confirmed=True,
        date_paid__gte=quarter_start,
        date_paid__lt=quarter_end,
    ).aggregate(Sum("amount"))

    return {
        "quarter": f"{quarter[0]} Q{quarter[1]}",
        "users": len(user_set),
        "services": service_count,
        "payments": payment_data["amount__sum"],
        "beneficiaries": len(beneficiary_set),
    }


@login_required
@user_passes_test(lambda user: user.is_superuser)
@require_GET
def admin_report(request):
    table_data = []
    quarters = _get_quarters_since_start()
    for q in quarters:
        data = _get_table_data_for_quarter(q)
        table_data.append(data)
    table = AdminReportTable(table_data)
    return render(request, "reports/admin.html", context={"table": table})
