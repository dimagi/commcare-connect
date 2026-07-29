import datetime
from decimal import Decimal

import pytest
from django.test import RequestFactory
from django_tables2 import RequestConfig

from commcare_connect.opportunity.helpers import get_worker_tasks_base_queryset
from commcare_connect.opportunity.models import (
    AssignedTaskStatus,
    CompletedWork,
    Opportunity,
    OpportunityAccess,
    PaymentUnit,
)
from commcare_connect.opportunity.tables import (
    InvoiceDeliveriesTable,
    InvoiceLineItemsTable,
    WorkerTasksTable,
)
from commcare_connect.opportunity.tests.factories import (
    AssignedTaskFactory,
    OpportunityAccessFactory,
    TaskTypeFactory,
    UserInviteFactory,
)
from commcare_connect.opportunity.utils.invoice_line_items import DeliveryRow, LineItem, Money
from commcare_connect.users.models import User


@pytest.mark.parametrize("show_org", [False, True])
def test_invoice_line_items_table_org_column_visibility(show_org):
    table = InvoiceLineItemsTable("KES", [], show_org=show_org)
    visible = [column.name for column in table.columns]
    assert ("flw_amount_local" in visible) is show_org
    assert ("org_amount_local" in visible) is show_org
    assert "total_amount_local" in visible
    assert table.columns["total_amount_local"].column.verbose_name == "Total Pay (KES)"
    if show_org:
        assert table.columns["flw_amount_local"].column.verbose_name == "FLW Pay (KES)"
        assert table.columns["org_amount_local"].column.verbose_name == "Org Pay (KES)"


def test_invoice_line_items_table_reads_money_off_line_items():
    line_item = LineItem(
        month=datetime.date(2026, 1, 1),
        payment_unit_name="Household visit",
        number_approved=2,
        flw_pay=Money(Decimal("40"), Decimal("4")),
        org_pay=Money(Decimal("10"), Decimal("1")),
        exchange_rate=Decimal("10"),
    )
    table = InvoiceLineItemsTable("KES", [line_item], show_org=True)

    rows = list(table.as_values())
    row = dict(zip(rows[0], rows[1]))

    assert row["FLW Pay (KES)"] == Decimal("40")
    assert row["Org Pay (KES)"] == Decimal("10")
    assert row["Total Pay (KES)"] == Decimal("50")  # 40 + 10
    assert row["Number Approved"] == 2


@pytest.mark.parametrize("show_org", [False, True])
def test_invoice_deliveries_table_org_column_visibility(show_org):
    table = InvoiceDeliveriesTable("KES", [], show_org=show_org)
    headers = next(table.as_values())
    assert ("FLW Pay (KES)" in headers) is show_org
    assert ("Org Pay (KES)" in headers) is show_org
    assert "Total Pay (KES)" in headers
    assert "Total Pay (USD)" in headers


def test_invoice_deliveries_table_total_folds_in_org_pay():
    delivery = DeliveryRow(
        completed_work=CompletedWork(
            entity_name="Baby A",
            date_created=datetime.datetime(2026, 1, 10, tzinfo=datetime.UTC),
            status_modified_date=datetime.datetime(2026, 1, 15, tzinfo=datetime.UTC),
            payment_unit=PaymentUnit(name="Household visit", opportunity=Opportunity(name="Test opp")),
            opportunity_access=OpportunityAccess(user=User(name="flw")),
        ),
        billed_count=1,
        flw_pay=Money(Decimal("40"), Decimal("4")),
        org_pay=Money(Decimal("10"), Decimal("1")),
    )
    table = InvoiceDeliveriesTable("KES", [delivery], show_org=True)

    rows = list(table.as_values())
    headers, values = rows[0], rows[1]
    row = dict(zip(headers, values))

    assert row["FLW Pay (KES)"] == 40
    assert row["Org Pay (KES)"] == 10
    assert row["Total Pay (KES)"] == 50  # 40 + 10
    assert row["Total Pay (USD)"] == 5  # 4 + 1


def _make_table(opportunity, per_page=25):
    data = get_worker_tasks_base_queryset(opportunity)
    table = WorkerTasksTable(data, org_slug="test-org", opp_id=opportunity.opportunity_id)
    rf = RequestFactory()
    request = rf.get("/")
    RequestConfig(request, paginate={"per_page": per_page}).configure(table)
    return table


@pytest.mark.django_db
def test_worker_tasks_table_groups_by_user(opportunity):
    access1 = OpportunityAccessFactory(opportunity=opportunity, accepted=True, user__name="Alice")
    UserInviteFactory(opportunity=opportunity, opportunity_access=access1, status="accepted")
    access2 = OpportunityAccessFactory(opportunity=opportunity, accepted=True, user__name="Bob")
    UserInviteFactory(opportunity=opportunity, opportunity_access=access2, status="invited")

    task_type = TaskTypeFactory(opportunity=opportunity, app=opportunity.deliver_app, is_active=True)
    AssignedTaskFactory(opportunity_access=access1, task_type=task_type, status=AssignedTaskStatus.ASSIGNED)
    AssignedTaskFactory(opportunity_access=access1, task_type=task_type, status=AssignedTaskStatus.COMPLETED)
    AssignedTaskFactory(opportunity_access=access2, task_type=task_type, status=AssignedTaskStatus.ASSIGNED)

    data = get_worker_tasks_base_queryset(opportunity)
    rows = list(data)
    assert len(rows) == 3

    # Alice's 2 tasks should come first (sorted by name), grouped together
    assert rows[0].pk == rows[1].pk == access1.pk
    assert rows[2].pk == access2.pk


@pytest.mark.django_db
def test_worker_tasks_table_empty(opportunity):
    table = _make_table(opportunity)
    rows = list(table.rows)
    assert len(rows) == 0
