from datetime import date

import pytest
from django.urls import reverse

from commcare_connect.opportunity.models import PaymentInvoice
from commcare_connect.opportunity.tests.factories import OpportunityFactory, PaymentInvoiceFactory
from commcare_connect.opportunity.utils.invoice import (
    filter_invoices_by_month,
    get_invoice_month_options,
    parse_invoice_month,
    resolve_invoice_month,
)
from commcare_connect.program.tests.factories import ProgramFactory

MAY = date(2026, 5, 1)
JUNE = date(2026, 6, 1)
JULY = date(2026, 7, 1)
AUGUST = date(2026, 8, 1)


@pytest.mark.parametrize(
    "value, expected",
    [
        ("2026-06", JUNE),
        ("", None),
        (None, None),
        ("June 2026", None),
        ("2026-13", None),
    ],
)
def test_parse_invoice_month(value, expected):
    assert parse_invoice_month(value) == expected


@pytest.mark.django_db
class TestFilterInvoicesByMonth:
    @pytest.fixture
    def opportunity(self):
        return OpportunityFactory()

    def _months_listing(self, invoice):
        qs = PaymentInvoice.objects.filter(opportunity=invoice.opportunity)
        return [month for month in (MAY, JUNE, JULY, AUGUST) if filter_invoices_by_month(qs, month).exists()]

    @pytest.mark.parametrize(
        "kwargs, expected_months",
        [
            # Service delivery: the period decides, generation date is ignored.
            (dict(start_date=date(2026, 6, 1), end_date=date(2026, 6, 30)), [JUNE]),
            (dict(start_date=date(2026, 5, 15), end_date=date(2026, 7, 10)), [MAY, JUNE, JULY]),
            (dict(start_date=date(2026, 6, 10), end_date=date(2026, 6, 20)), [JUNE]),
            (dict(start_date=date(2026, 6, 30), end_date=date(2026, 7, 1)), [JUNE, JULY]),
            # Service delivery with no period falls back to the generation date.
            (dict(start_date=None, end_date=None), [AUGUST]),
            (dict(start_date=date(2026, 6, 1), end_date=None), [AUGUST]),
        ],
    )
    def test_service_delivery(self, opportunity, kwargs, expected_months):
        invoice = PaymentInvoiceFactory(
            opportunity=opportunity, service_delivery=True, date=date(2026, 8, 3), **kwargs
        )
        assert self._months_listing(invoice) == expected_months

    @pytest.mark.parametrize(
        "kwargs, expected_months",
        [
            (dict(date_of_expense=date(2026, 5, 20)), [MAY]),
            # The period fields are not used for custom invoices even when set.
            (dict(date_of_expense=date(2026, 5, 20), start_date=date(2026, 6, 1), end_date=date(2026, 7, 31)), [MAY]),
            (dict(date_of_expense=None), [AUGUST]),
        ],
    )
    def test_custom(self, opportunity, kwargs, expected_months):
        invoice = PaymentInvoiceFactory(
            opportunity=opportunity, service_delivery=False, date=date(2026, 8, 3), **kwargs
        )
        assert self._months_listing(invoice) == expected_months


@pytest.mark.django_db
class TestGetInvoiceMonthOptions:
    def test_no_invoices(self):
        assert get_invoice_month_options(PaymentInvoice.objects.none()) == []

    def test_only_months_with_invoices_newest_first(self):
        opportunity = OpportunityFactory()
        PaymentInvoiceFactory(
            opportunity=opportunity,
            service_delivery=True,
            start_date=date(2026, 5, 1),
            end_date=date(2026, 6, 30),
            date=date(2026, 7, 2),
        )
        PaymentInvoiceFactory(
            opportunity=opportunity,
            service_delivery=False,
            date_of_expense=date(2026, 8, 15),
            date=date(2026, 8, 20),
        )
        qs = PaymentInvoice.objects.filter(opportunity=opportunity)
        assert get_invoice_month_options(qs) == [AUGUST, JUNE, MAY]

    @pytest.mark.parametrize(
        "kwargs",
        [
            dict(service_delivery=True, start_date=date(2026, 5, 15), end_date=date(2026, 7, 10)),
            dict(service_delivery=True, start_date=None, end_date=date(2026, 7, 10)),
            dict(service_delivery=False, date_of_expense=date(2026, 6, 3)),
            dict(service_delivery=False, date_of_expense=None),
        ],
    )
    def test_options_agree_with_the_filter(self, kwargs):
        """The chips are computed in Python and the list in SQL; every offered month must list the invoice."""
        invoice = PaymentInvoiceFactory(date=date(2026, 8, 3), **kwargs)
        qs = PaymentInvoice.objects.filter(pk=invoice.pk)
        options = get_invoice_month_options(qs)
        listed = [month for month in (MAY, JUNE, JULY, AUGUST) if filter_invoices_by_month(qs, month).exists()]
        assert sorted(options) == listed


@pytest.mark.parametrize(
    "month_param, highlight, expected",
    [
        (None, None, JULY),
        ("2026-05", None, MAY),
        ("all", None, None),
        (None, "INV-1", None),
        ("2026-05", "INV-1", None),
        ("bogus", None, JULY),
    ],
)
def test_resolve_invoice_month(month_param, highlight, expected):
    assert resolve_invoice_month(month_param, [JULY, JUNE, MAY], highlight) == expected


def test_resolve_invoice_month_without_options():
    assert resolve_invoice_month(None, []) is None


@pytest.mark.django_db
def test_the_chip_you_are_on_is_not_a_link(client, organization, org_user_member):
    """An active chip is current state, so it must not be focusable or activatable by keyboard."""
    opportunity = OpportunityFactory(program=ProgramFactory(organization=organization), organization=organization)
    PaymentInvoiceFactory(opportunity=opportunity, service_delivery=True, start_date=JUNE, end_date=date(2026, 6, 30))
    client.force_login(org_user_member)

    body = client.get(
        reverse("opportunity:invoice_list", args=(organization.slug, opportunity.opportunity_id))
    ).content.decode()

    active = [chip for chip in body.split("<a ")[1:] if "chip-active" in chip.split(">")[0]]
    assert len(active) == 1
    assert "href" not in active[0].split(">")[0]
    assert 'aria-current="true"' in active[0].split(">")[0]
