import datetime
from decimal import Decimal
from unittest import mock

import pytest
from django.utils.timezone import localdate

from commcare_connect.opportunity.models import (
    CompletedWork,
    CompletedWorkStatus,
    Currency,
    ExchangeRate,
    InvoiceStatus,
    OpportunityAccess,
    Payment,
    PaymentInvoice,
    VisitReviewStatus,
    VisitValidationStatus,
)
from commcare_connect.opportunity.tests.factories import (
    CompletedWorkFactory,
    DeliverUnitFactory,
    OpportunityAccessFactory,
    OpportunityFactory,
    PaymentUnitFactory,
    UserVisitFactory,
)
from commcare_connect.opportunity.visit_import import ImportException
from commcare_connect.program.tests.factories import ProgramFactory
from commcare_connect.simulation.fakes import payments as payment_fakes
from commcare_connect.simulation.steps.browser import StepRequestError
from commcare_connect.simulation.steps.payments import PAYMENT_CHANNELS
from commcare_connect.users.tests.factories import MembershipFactory, MobileUserFactory, UserFactory
from commcare_connect.utils.test_utils import grant_all_org_access

PAY_DAY = datetime.date(2026, 10, 1)
NEXT_PAY_DAY = datetime.date(2026, 10, 8)
# When the prerequisite work was approved, so that the invoice step's default end date (yesterday) covers it.
APPROVED_AT = datetime.datetime(2026, 9, 10, 12, tzinfo=datetime.UTC)


@pytest.fixture
def sent_messages():
    """What the ConnectID stand-in was asked to push. Request it before ``sim``, which starts the stand-ins."""
    with mock.patch.object(payment_fakes, "fake_send_message_bulk") as send:
        yield send


@pytest.fixture
def opportunity(user, organization, program_manager_org):
    """A managed opportunity; ``user`` administers both the network-manager and the program-manager organization."""
    MembershipFactory(user=user, organization=organization)
    MembershipFactory(user=user, organization=program_manager_org)
    return OpportunityFactory(
        organization=organization, program=ProgramFactory(organization=program_manager_org), managed=True
    )


def _user(kind, opportunity):
    """Someone the invoice pages should turn away.

    ``outsider`` belongs to neither organization, so the access check answers 404. ``all_org_access`` passes that
    check without belonging to any organization, and ``pm_member`` passes it as a non-admin member of the program
    manager's organization; the views answer both with a 302 redirect.
    """
    user = UserFactory()
    if kind == "all_org_access":
        grant_all_org_access(user)
    elif kind == "pm_member":
        MembershipFactory(user=user, organization=opportunity.program.organization, role="member")
    return user


@pytest.fixture
def outsider(opportunity):
    return _user("outsider", opportunity)


@pytest.fixture
def workers(sim, opportunity):
    """Two workers with work approved at ``APPROVED_AT`` and unpaid: 4 visits and 2 visits at 10, plus an org fee
    of 2 a visit."""
    payment_unit = PaymentUnitFactory(opportunity=opportunity, amount=10, org_amount=2)
    deliver_unit = DeliverUnitFactory(app=opportunity.deliver_app, payment_unit=payment_unit)
    with sim.at(APPROVED_AT):
        return [_approved_work(opportunity, deliver_unit, visits) for visits in (4, 2)]


def _approved_work(opportunity, deliver_unit, visits):
    """A worker whose ``visits`` approved, PM-agreed visits make up one approved, unpaid completed work."""
    payment_unit = deliver_unit.payment_unit
    worker = {"opportunity": opportunity, "user": MobileUserFactory()}
    access = OpportunityAccessFactory(**worker, accepted=True, payment_accrued=visits * payment_unit.amount)
    work = CompletedWorkFactory(
        opportunity_access=access,
        payment_unit=payment_unit,
        status=CompletedWorkStatus.approved,
        saved_completed_count=visits,
        saved_approved_count=visits,
        saved_payment_accrued=visits * payment_unit.amount,
    )
    approved = {"status": VisitValidationStatus.approved, "review_status": VisitReviewStatus.agree}
    UserVisitFactory.create_batch(
        visits, **worker, **approved, opportunity_access=access, deliver_unit=deliver_unit, completed_work=work
    )
    return worker["user"]


@pytest.fixture
def invoice(sim, opportunity, workers):
    return sim.invoice(opportunity)


@pytest.fixture
def ready_invoice(sim, invoice):
    return _advance(sim, invoice, InvoiceStatus.PENDING_PM_REVIEW, InvoiceStatus.READY_TO_PAY)


def _advance(sim, invoice, *statuses):
    for status in statuses:
        sim.invoice_status(invoice, status)
    return invoice


def _assert_unpaid(invoice, status):
    assert PaymentInvoice.objects.filter(pk=invoice.pk, status=status, payment__isnull=True).exists()


def _paid_by_user(payments):
    return {payment.opportunity_access.user: payment.amount for payment in payments}


@pytest.mark.django_db
class TestPay:
    def test_pays_share_of_what_is_unpaid(self, sent_messages, sim, opportunity, workers):
        first, second = workers

        half = sim.pay(opportunity, fraction=0.5, payment_date=PAY_DAY)
        # The import refuses a payment matching a worker's last one in amount and date, so pay the rest later.
        rest = sim.pay(opportunity, payment_date=NEXT_PAY_DAY)

        assert _paid_by_user(half) == {first: Decimal("20"), second: Decimal("10")}
        assert _paid_by_user(rest) == {first: Decimal("20"), second: Decimal("10")}
        assert {payment.date_paid.date() for payment in half} == {PAY_DAY}
        assert {payment.date_paid.date() for payment in rest} == {NEXT_PAY_DAY}
        for payment in half + rest:
            assert (payment.payment_method, payment.payment_operator) in PAYMENT_CHANNELS
        assert sent_messages.call_count == 2

    def test_full_payment_dates_the_work_it_covers(self, sent_messages, sim, opportunity, workers):
        sim.pay(opportunity, fraction=0.5, payment_date=PAY_DAY)
        assert not CompletedWork.objects.filter(payment_date__isnull=False).exists()

        sim.pay(opportunity, payment_date=NEXT_PAY_DAY)

        assert {work.payment_date.date() for work in CompletedWork.objects.all()} == {NEXT_PAY_DAY}

    def test_pays_only_the_named_workers(self, sent_messages, sim, opportunity, workers):
        payments = sim.pay(opportunity, workers[1:])

        assert _paid_by_user(payments) == {workers[1]: Decimal("20")}
        assert payments[0].date_paid.date() == localdate()

    def test_nothing_to_pay(self, sent_messages, sim, opportunity, workers):
        sim.pay(opportunity)

        assert sim.pay(opportunity) == []
        assert sent_messages.call_count == 1

    def test_local_currency_is_converted_at_the_stand_in_rate(self, sim, workers, opportunity):
        opportunity.currency = Currency.objects.get(code="KES")
        opportunity.save()

        payments = sim.pay(opportunity, payment_date=PAY_DAY)

        rate = Decimal(str(payment_fakes.FIXED_RATES["KES"]))
        assert {payment.amount_usd for payment in payments} == {
            (amount / rate).quantize(Decimal("0.01")) for amount in (Decimal("40"), Decimal("20"))
        }
        # The app fetched and stored the rates itself, rather than finding one seeded for it. It stores one per month
        # it looks up, which can include the real current month, so there may be more than one.
        assert set(ExchangeRate.objects.filter(currency_code="KES").values_list("rate", flat=True)) == {rate}

    def test_refused_import_pays_no_one(self, sent_messages, sim, opportunity, workers):
        OpportunityAccess.objects.filter(user=workers[1]).update(suspended=True)

        with pytest.raises(ImportException):
            sim.pay(opportunity)

        assert not Payment.objects.exists()
        sent_messages.assert_not_called()


@pytest.mark.django_db
class TestInvoice:
    def test_bills_approved_work(self, sim, opportunity, workers):
        invoice = sim.invoice(opportunity)

        assert invoice.status == InvoiceStatus.PENDING_NM_REVIEW
        assert invoice.service_delivery
        # 6 visits at 10 to the workers and 2 to the network manager.
        assert invoice.amount == Decimal("72")
        assert sorted(invoice.work_items.values_list("billed_count", flat=True)) == [2, 4]
        assert all(work.invoiced_approved_count == work.saved_approved_count for work in CompletedWork.objects.all())

    def test_bills_work_only_once(self, sim, opportunity, workers):
        sim.invoice(opportunity)

        assert sim.invoice(opportunity) is None

    def test_work_approved_today_waits_for_tomorrows_invoice(self, sim, opportunity, workers):
        with sim.at(APPROVED_AT):
            assert sim.invoice(opportunity) is None
        with sim.at(APPROVED_AT + datetime.timedelta(days=1)):
            assert sim.invoice(opportunity).end_date == APPROVED_AT.date()

    def test_end_date_bounds_the_work_billed(self, sim, opportunity, workers):
        assert sim.invoice(opportunity, end_date=APPROVED_AT.date() - datetime.timedelta(days=1)) is None

    def test_page_refuses_a_user_outside_the_network_manager_org(self, sim, opportunity, workers, outsider):
        sim.nm_admin = outsider

        with pytest.raises(StepRequestError):
            sim.invoice(opportunity)

        assert not PaymentInvoice.objects.exists()
        assert not CompletedWork.objects.filter(invoiced_approved_count__gt=0).exists()


@pytest.mark.django_db
class TestInvoiceStatus:
    @pytest.fixture
    def nm_admin(self, sim, opportunity):
        """A network-manager user apart from the program manager, so the acting role shows on the audit event."""
        sim.nm_admin = MembershipFactory(organization=opportunity.organization).user
        return sim.nm_admin

    @pytest.mark.parametrize(
        "path, acted_as_pm, released",
        [
            ([InvoiceStatus.PENDING_PM_REVIEW], False, False),
            ([InvoiceStatus.CANCELLED_BY_NM], False, True),
            ([InvoiceStatus.PENDING_PM_REVIEW, InvoiceStatus.READY_TO_PAY], True, False),
            ([InvoiceStatus.PENDING_PM_REVIEW, InvoiceStatus.REJECTED_BY_PM], True, True),
        ],
    )
    def test_allowed_transition_as_its_role(self, sim, invoice, nm_admin, path, acted_as_pm, released):
        _advance(sim, invoice, *path)

        invoice.refresh_from_db()
        assert invoice.status == path[-1]
        expected_actor = sim.pm_admin if acted_as_pm else nm_admin
        assert invoice.status_events.last().pgh_context.metadata["username"] == expected_actor.username
        assert invoice.work_items.exists() is not released

    def test_submission_is_attested(self, sim, invoice):
        sim.invoice_status(invoice, InvoiceStatus.PENDING_PM_REVIEW)

        assert invoice.status_events.last().pgh_context.metadata["attestation_certified"] is True

    @pytest.mark.parametrize(
        "path, refused",
        [
            ([], InvoiceStatus.READY_TO_PAY),
            ([], InvoiceStatus.REJECTED_BY_PM),
            ([InvoiceStatus.PENDING_PM_REVIEW], InvoiceStatus.CANCELLED_BY_NM),
            ([InvoiceStatus.CANCELLED_BY_NM], InvoiceStatus.PENDING_PM_REVIEW),
        ],
    )
    def test_refused_transition_changes_nothing(self, sim, invoice, path, refused):
        _advance(sim, invoice, *path)
        before = path[-1] if path else InvoiceStatus.PENDING_NM_REVIEW

        with pytest.raises(StepRequestError, match="Invalid status transition"):
            sim.invoice_status(invoice, refused)

        invoice.refresh_from_db()
        assert invoice.status == before

    @pytest.mark.parametrize("kind, status_code", [("outsider", 404), ("all_org_access", 302)])
    def test_page_turns_away_a_user_not_allowed_to_review(self, sim, opportunity, invoice, kind, status_code):
        _advance(sim, invoice, InvoiceStatus.PENDING_PM_REVIEW)
        sim.pm_admin = _user(kind, opportunity)

        with pytest.raises(StepRequestError, match=f": {status_code}"):
            sim.invoice_status(invoice, InvoiceStatus.READY_TO_PAY)

        assert PaymentInvoice.objects.get(pk=invoice.pk).status == InvoiceStatus.PENDING_PM_REVIEW


@pytest.mark.django_db
class TestInvoicePay:
    def test_refuses_invoice_not_ready_to_pay(self, sim, invoice):
        with pytest.raises(StepRequestError, match="Ready to Pay"):
            sim.invoice_pay(invoice)

        _assert_unpaid(invoice, InvoiceStatus.PENDING_NM_REVIEW)

    @pytest.mark.parametrize("kind, status_code", [("outsider", 404), ("pm_member", 302)])
    def test_page_turns_away_a_user_not_allowed_to_pay(self, sim, opportunity, ready_invoice, kind, status_code):
        sim.pm_admin = _user(kind, opportunity)

        with pytest.raises(StepRequestError, match=f": {status_code}"):
            sim.invoice_pay(ready_invoice)

        _assert_unpaid(ready_invoice, InvoiceStatus.READY_TO_PAY)

    def test_paying_twice_is_refused(self, sim, ready_invoice):
        sim.invoice_pay(ready_invoice)

        with pytest.raises(StepRequestError):
            sim.invoice_pay(ready_invoice)

        assert Payment.objects.filter(invoice=ready_invoice).count() == 1

    def test_pays_ready_invoice(self, sim, ready_invoice, mailoutbox):
        invoice = sim.invoice_pay(ready_invoice)

        payment = Payment.objects.get(invoice=invoice, invoice__status=InvoiceStatus.PAID)
        assert (payment.amount, payment.organization) == (invoice.amount, invoice.opportunity.organization)
        assert [message.subject for message in mailoutbox] == [
            f"[{invoice.opportunity.name}] Invoice {invoice.invoice_number} Has Been Paid"
        ]


@pytest.mark.django_db
class TestClock:
    """Single-action steps run at the caller's moment, so what the app stamps follows ``sim.at``."""

    MOMENT = datetime.datetime(2026, 9, 20, 12, tzinfo=datetime.UTC)

    def test_payment_is_dated_at_the_moment(self, sent_messages, sim, opportunity, workers):
        with sim.at(self.MOMENT):
            (payment, _) = sim.pay(opportunity)

        assert payment.date_paid.date() == self.MOMENT.date()

    def test_invoice_lifecycle_is_dated_at_the_moment(self, sim, opportunity, workers):
        with sim.at(self.MOMENT):
            invoice = sim.invoice(opportunity)
            sim.invoice_status(invoice, InvoiceStatus.PENDING_PM_REVIEW)
            sim.invoice_status(invoice, InvoiceStatus.READY_TO_PAY)
            sim.invoice_pay(invoice)

        today = self.MOMENT.date()
        assert (invoice.date, invoice.end_date) == (today, today - datetime.timedelta(days=1))
        assert invoice.start_date == APPROVED_AT.date().replace(day=1)
        assert Payment.objects.get(invoice=invoice).date_paid.date() == today


def test_unknown_currency_is_priced_at_par():
    rates = payment_fakes.fake_request_rates("https://openexchangerates.org/api/x.json")

    assert rates["XYZ"] == rates.get("XYZ") == 1
