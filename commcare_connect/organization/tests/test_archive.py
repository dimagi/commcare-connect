from datetime import UTC, date, datetime, timedelta
from unittest import mock

import pytest
from django.db import DatabaseError

from commcare_connect.opportunity.models import InvoiceStatus, Opportunity, VisitReviewStatus, VisitValidationStatus
from commcare_connect.opportunity.tests.factories import (
    OpportunityAccessFactory,
    OpportunityFactory,
    PaymentFactory,
    PaymentInvoiceFactory,
    UserVisitFactory,
)
from commcare_connect.organization.archive import (
    ArchiveBlocker,
    ArchiveBlockerKind,
    archive_blockers,
    archive_organization,
    live_or_upcoming_opportunity_q,
    live_or_upcoming_program_q,
    restore_organization,
)
from commcare_connect.organization.models import Contact, Organization
from commcare_connect.program.models import Program, ProgramApplicationStatus
from commcare_connect.program.tests.factories import ProgramApplicationFactory, ProgramFactory
from commcare_connect.users.tests.factories import ContactFactory, OrganizationFactory

STAFF_EMAIL = "staff@example.com"
TODAY = date(2026, 10, 27)


@pytest.mark.django_db
class TestArchiveOrganization:
    def test_archives_the_organization_and_its_active_contacts(self, organization):
        active = ContactFactory(organization=organization)
        already_archived = ContactFactory(organization=organization, is_archived=True)
        other_organization_contact = ContactFactory()

        archive_organization(organization, user_email=STAFF_EMAIL, reason="")

        organization.refresh_from_db()
        assert organization.is_archived
        assert organization.modified_by == STAFF_EMAIL
        active = Contact.objects.get(pk=active.pk)
        assert active.is_archived
        assert active.modified_by == STAFF_EMAIL
        assert Contact.objects.get(pk=already_archived.pk).is_archived
        assert not Contact.objects.get(pk=other_organization_contact.pk).is_archived

    @pytest.mark.parametrize(
        "notes, expected",
        [
            ("", "[System Note] Archived on 2026-10-27 by staff@example.com: Duplicate"),
            ("Existing", "Existing\n[System Note] Archived on 2026-10-27 by staff@example.com: Duplicate"),
        ],
    )
    @mock.patch("commcare_connect.organization.archive.timezone.now", return_value=datetime(2026, 10, 27, tzinfo=UTC))
    def test_reason_is_appended_as_a_system_note(self, _now, notes, expected):
        organization = OrganizationFactory(notes=notes)

        archive_organization(organization, user_email=STAFF_EMAIL, reason="Duplicate")

        organization.refresh_from_db()
        assert organization.notes == expected

    def test_no_reason_leaves_notes_unchanged(self):
        organization = OrganizationFactory(notes="Existing")

        archive_organization(organization, user_email=STAFF_EMAIL, reason="  ")

        organization.refresh_from_db()
        assert organization.notes == "Existing"

    def test_a_failed_save_leaves_the_contacts_unarchived(self, organization):
        contact = ContactFactory(organization=organization)

        with mock.patch.object(Organization, "save", side_effect=DatabaseError), pytest.raises(DatabaseError):
            archive_organization(organization, user_email=STAFF_EMAIL, reason="")

        assert not Contact.objects.get(pk=contact.pk).is_archived

    def test_records_an_update_event(self, organization):
        archive_organization(organization, user_email=STAFF_EMAIL, reason="")

        assert organization.events.latest("pgh_id").is_archived


@pytest.mark.django_db
class TestRestoreOrganization:
    def test_restores_the_organization_and_leaves_its_contacts_archived(self):
        organization = OrganizationFactory(is_archived=True)
        contact = ContactFactory(organization=organization, is_archived=True)

        restore_organization(organization, user_email=STAFF_EMAIL, reason="")

        organization.refresh_from_db()
        assert not organization.is_archived
        assert organization.modified_by == STAFF_EMAIL
        assert Contact.objects.get(pk=contact.pk).is_archived

    @mock.patch("commcare_connect.organization.archive.timezone.now", return_value=datetime(2026, 10, 27, tzinfo=UTC))
    def test_reason_is_appended_as_a_system_note(self, _now):
        organization = OrganizationFactory(is_archived=True)

        restore_organization(organization, user_email=STAFF_EMAIL, reason="Back")

        organization.refresh_from_db()
        assert organization.notes == "[System Note] Restored on 2026-10-27 by staff@example.com: Back"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "end_date, active, archived, is_test, expected",
    [
        (TODAY, True, False, False, True),
        # A future end date blocks whether or not the opportunity is switched on or a test.
        (TODAY + timedelta(days=30), False, False, False, True),
        (TODAY + timedelta(days=30), True, False, True, True),
        # An archived opportunity is finished.
        (TODAY + timedelta(days=30), True, True, False, False),
        (None, True, True, False, False),
        (None, True, False, False, True),
        (None, False, False, False, False),
        (TODAY - timedelta(days=1), True, False, False, False),
    ],
)
def test_live_or_upcoming_opportunity_q(end_date, active, archived, is_test, expected):
    opportunity = OpportunityFactory(end_date=end_date, active=active, archived=archived, is_test=is_test)

    matched = Opportunity.objects.filter(live_or_upcoming_opportunity_q(TODAY), pk=opportunity.pk).exists()

    assert matched == expected


@pytest.mark.django_db
@pytest.mark.parametrize(
    "end_date, expected",
    [(TODAY, True), (TODAY + timedelta(days=1), True), (TODAY - timedelta(days=1), False)],
)
def test_live_or_upcoming_program_q(end_date, expected):
    program = ProgramFactory(start_date=TODAY - timedelta(days=60), end_date=end_date)

    assert Program.objects.filter(live_or_upcoming_program_q(TODAY), pk=program.pk).exists() == expected


def _blocker(record, roles, reasons=("Live or upcoming",)):
    kind = ArchiveBlockerKind.PROGRAM if isinstance(record, Program) else ArchiveBlockerKind.OPPORTUNITY
    return ArchiveBlocker(name=record.name, kind=kind, roles=roles, reasons=list(reasons))


def _ended_program(**kwargs):
    today = date.today()
    return ProgramFactory(start_date=today - timedelta(days=60), end_date=today - timedelta(days=1), **kwargs)


def _ended_opportunity(**kwargs):
    return OpportunityFactory(end_date=date.today() - timedelta(days=1), **kwargs)


@pytest.mark.django_db
class TestArchiveBlockers:
    @pytest.mark.parametrize(
        "role", ["Program owner", "Program funder", "Network manager", "Supervising organization"]
    )
    def test_each_role_on_live_work_blocks(self, organization, role):
        if role == "Program owner":
            blocker = _blocker(ProgramFactory(organization=organization), [role])
        elif role == "Program funder":
            blocker = _blocker(ProgramFactory(funder=organization), [role])
        elif role == "Network manager":
            opportunity = OpportunityFactory(organization=organization, program=_ended_program())
            blocker = _blocker(opportunity, [role])
        else:
            opportunity = OpportunityFactory(supervising_organization=organization, program=_ended_program())
            blocker = _blocker(opportunity, [role])

        assert archive_blockers(organization) == [blocker]

    def test_live_opportunity_under_an_ended_owned_program_blocks(self, organization):
        # The program's owner supervises its opportunities unless another organization is chosen.
        opportunity = OpportunityFactory(program=_ended_program(organization=organization))

        assert archive_blockers(organization) == [_blocker(opportunity, ["Program owner", "Supervising organization"])]

    def test_accepted_application_and_watching_do_not_block(self, organization):
        program = ProgramFactory()
        ProgramApplicationFactory(program=program, organization=organization, status=ProgramApplicationStatus.ACCEPTED)
        program.watchers.add(organization)

        assert archive_blockers(organization) == []

    def test_finished_work_does_not_block(self, organization):
        _ended_opportunity(program=_ended_program(organization=organization))

        assert archive_blockers(organization) == []


def _invoice(status):
    return lambda opportunity: PaymentInvoiceFactory(opportunity=opportunity, status=status)


def _payment(accrued, paid, confirmed=True):
    def create(opportunity):
        access = OpportunityAccessFactory(opportunity=opportunity, payment_accrued=accrued)
        PaymentFactory(opportunity_access=access, amount=paid, confirmed=confirmed)

    return create


def _visit(status, review_status=VisitReviewStatus.agree):
    def create(opportunity):
        UserVisitFactory(
            opportunity=opportunity,
            opportunity_access=OpportunityAccessFactory(opportunity=opportunity),
            status=status,
            review_status=review_status,
        )

    return create


@pytest.mark.django_db
class TestUnsettledWorkBlocksArchiving:
    @pytest.mark.parametrize(
        "create_unsettled, reason",
        [
            (_invoice(InvoiceStatus.PENDING_NM_REVIEW), "Unsettled invoices"),
            (_invoice(InvoiceStatus.PENDING_PM_REVIEW), "Unsettled invoices"),
            (_invoice(InvoiceStatus.READY_TO_PAY), "Unsettled invoices"),
            # A status added later counts as unsettled until it is listed as settled.
            (_invoice("awaiting_finance"), "Unsettled invoices"),
            (_payment(accrued=100, paid=40), "Payments owed to workers"),
            (_visit(VisitValidationStatus.pending), "Visits awaiting review"),
            (_visit(VisitValidationStatus.approved, VisitReviewStatus.pending), "Visits awaiting review"),
        ],
    )
    def test_unsettled_work_on_a_finished_opportunity_blocks(self, organization, create_unsettled, reason):
        opportunity = _ended_opportunity(organization=organization, program=_ended_program())
        create_unsettled(opportunity)

        assert archive_blockers(organization) == [_blocker(opportunity, ["Network manager"], [reason])]

    @pytest.mark.parametrize(
        "create_settled",
        [
            _invoice(InvoiceStatus.PAID),
            _invoice(InvoiceStatus.CANCELLED_BY_NM),
            _invoice(InvoiceStatus.REJECTED_BY_PM),
            _invoice(InvoiceStatus.ARCHIVED),
            _payment(accrued=100, paid=100, confirmed=False),
            _visit(VisitValidationStatus.approved, VisitReviewStatus.agree),
            _visit(VisitValidationStatus.rejected, VisitReviewStatus.pending),
        ],
    )
    def test_settled_work_does_not_block(self, organization, create_settled):
        create_settled(_ended_opportunity(organization=organization, program=_ended_program()))

        assert archive_blockers(organization) == []

    def test_visits_of_an_automatically_verified_opportunity_do_not_block(self, organization):
        opportunity = _ended_opportunity(
            organization=organization, program=_ended_program(), automatic_visit_verification=True
        )
        _visit(VisitValidationStatus.pending)(opportunity)

        assert archive_blockers(organization) == []

    def test_live_opportunity_with_an_unsettled_invoice_is_one_blocker_with_both_reasons(self, organization):
        opportunity = OpportunityFactory(organization=organization, program=_ended_program())
        _invoice(InvoiceStatus.READY_TO_PAY)(opportunity)

        assert archive_blockers(organization) == [
            _blocker(opportunity, ["Network manager"], ["Live or upcoming", "Unsettled invoices"])
        ]
