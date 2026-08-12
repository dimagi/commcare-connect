from datetime import timedelta

import pytest
from django.utils import timezone

from commcare_connect.opportunity.models import LabsRecord
from commcare_connect.opportunity.tests.factories import CommCareAppFactory, OpportunityFactory, PaymentFactory
from commcare_connect.organization.merge import (
    SIMPLE_REASSIGNMENTS,
    MergeNotAllowed,
    _move_program_watchers,
    merge_organizations,
)
from commcare_connect.organization.models import Organization, OrganizationInvite, UserOrganizationMembership
from commcare_connect.program.models import ProgramApplication, ProgramApplicationStatus
from commcare_connect.program.tests.factories import ProgramApplicationFactory, ProgramFactory
from commcare_connect.users.tests.factories import (
    MembershipFactory,
    OrganizationFactory,
    OrganizationInviteFactory,
    UserFactory,
)

pytestmark = pytest.mark.django_db

Status = ProgramApplicationStatus


@pytest.fixture
def source():
    return OrganizationFactory(name="Source Workspace")


@pytest.fixture
def target():
    return OrganizationFactory(name="Target Workspace")


class TestMergeGuards:
    def test_self_merge_is_refused(self, source):
        with pytest.raises(MergeNotAllowed):
            merge_organizations(source, source)

        assert Organization.objects.filter(pk=source.pk).exists()

    def test_unsaved_organization_is_refused(self, target):
        with pytest.raises(MergeNotAllowed):
            merge_organizations(Organization(name="Never Saved"), target)


class TestSourceRemoval:
    def test_source_is_deleted_and_target_survives(self, source, target):
        summary = merge_organizations(source, target)

        assert not Organization.objects.filter(pk=source.pk).exists()
        assert Organization.objects.filter(pk=target.pk).exists()
        assert summary.source_slug == source.slug
        assert summary.target_slug == target.slug


class TestProfileFields:
    """The target keeps its own profile; the source's is discarded."""

    @pytest.mark.parametrize("source_value", [True, False])
    @pytest.mark.parametrize("target_value", [True, False])
    @pytest.mark.parametrize("capability", ["program_manager", "funder"])
    def test_capability_flag_keeps_the_target_value(self, capability, source_value, target_value):
        source = OrganizationFactory(**{capability: source_value})
        target = OrganizationFactory(**{capability: target_value})

        merge_organizations(source, target)

        target.refresh_from_db()
        assert getattr(target, capability) is target_value

    def test_no_profile_field_is_taken_from_the_source(self, target):
        """A sweep, so a profile field added to Organization later is covered without touching this test."""
        source = OrganizationFactory(name="Source Workspace", program_manager=True, funder=True)
        target.refresh_from_db()
        before = _profile_snapshot(target)

        merge_organizations(source, target)

        target.refresh_from_db()
        assert _profile_snapshot(target) == before


def _profile_snapshot(organization: Organization) -> dict:
    return {field.attname: getattr(organization, field.attname) for field in organization._meta.concrete_fields}


class TestSimpleReassignments:
    def test_every_plain_foreign_key_is_repointed(self, source, target):
        commcare_app = CommCareAppFactory(organization=source)
        opportunity = OpportunityFactory(organization=source)
        supervised = OpportunityFactory(supervising_organization=source)
        payment = PaymentFactory(organization=source)
        labs_record = LabsRecord.objects.create(experiment="merge-test", type="note", data={}, organization=source)
        owned_program = ProgramFactory(organization=source)
        funded_program = ProgramFactory(funder=source)

        merge_organizations(source, target)

        for obj, field_name in [
            (commcare_app, "organization"),
            (opportunity, "organization"),
            (supervised, "supervising_organization"),
            (payment, "organization"),
            (labs_record, "organization"),
            (owned_program, "organization"),
            (funded_program, "funder"),
        ]:
            obj.refresh_from_db()
            assert getattr(obj, field_name) == target, f"{type(obj).__name__}.{field_name} was not reassigned"

    def test_summary_reports_a_count_for_every_listed_relation(self, source, target):
        ProgramFactory(organization=source)

        summary = merge_organizations(source, target)

        assert set(summary.reassigned) == {relation.label for relation in SIMPLE_REASSIGNMENTS}
        assert summary.reassigned["program.Program.organization"] == 1
        assert summary.reassigned["opportunity.Payment.organization"] == 0


class TestProgramWatchers:
    def test_watched_programs_move_to_the_target(self, source, target):
        program = ProgramFactory()
        program.watchers.add(source)

        summary = merge_organizations(source, target)

        assert list(program.watchers.all()) == [target]
        assert summary.programs_watched == 1

    def test_no_duplicate_when_both_organizations_watch(self, source, target):
        program = ProgramFactory()
        program.watchers.add(source, target)

        moved = _move_program_watchers(source, target)

        assert moved == 1
        # Asserted without merge_organizations, so the source's removal is
        # attributable to the helper rather than to source.delete()'s cascade.
        assert list(program.watchers.all()) == [target]


class TestProgramApplications:
    def test_source_only_application_moves(self, source, target):
        application = ProgramApplicationFactory(organization=source)

        summary = merge_organizations(source, target)

        application.refresh_from_db()
        assert application.organization == target
        assert summary.applications_moved == 1
        assert summary.applications_deduped == 0

    @pytest.mark.parametrize(
        ("source_status", "target_status", "expected"),
        [
            (Status.ACCEPTED, Status.REJECTED, Status.ACCEPTED),
            (Status.INVITED, Status.ACCEPTED, Status.ACCEPTED),
            (Status.INVITED, Status.APPLIED, Status.APPLIED),
            (Status.DECLINED, Status.REJECTED, Status.DECLINED),
            (Status.APPLIED, Status.APPLIED, Status.APPLIED),
        ],
    )
    def test_conflict_keeps_the_most_advanced_status(self, source, target, source_status, target_status, expected):
        program = ProgramFactory()
        ProgramApplicationFactory(program=program, organization=source, status=source_status)
        ProgramApplicationFactory(program=program, organization=target, status=target_status)

        summary = merge_organizations(source, target)

        applications = ProgramApplication.objects.filter(program=program)
        assert applications.count() == 1, "duplicates would break invite_organization's update_or_create"
        surviving = applications.get()
        assert surviving.organization == target
        assert surviving.status == expected
        assert summary.applications_deduped == 1

    def test_duplicate_source_applications_collapse_to_one(self, source, target):
        """The source can hold duplicates: nothing in the DB or admin prevents it."""
        program = ProgramFactory()
        ProgramApplicationFactory(program=program, organization=source, status=Status.INVITED)
        ProgramApplicationFactory(program=program, organization=source, status=Status.ACCEPTED)

        summary = merge_organizations(source, target)

        applications = ProgramApplication.objects.filter(program=program)
        assert applications.count() == 1
        assert applications.get().status == Status.ACCEPTED
        assert (summary.applications_moved, summary.applications_deduped) == (1, 1)

    def test_application_to_a_program_the_target_now_owns_is_removed(self, source, target):
        program = ProgramFactory(organization=source)
        ProgramApplicationFactory(program=program, organization=target)

        summary = merge_organizations(source, target)

        program.refresh_from_db()
        assert program.organization == target
        assert not ProgramApplication.objects.filter(program=program).exists()
        assert summary.self_applications_removed == 1


class TestMemberships:
    def test_source_only_membership_moves(self, source, target):
        membership = MembershipFactory(organization=source, role="member")

        summary = merge_organizations(source, target)

        membership.refresh_from_db()
        assert membership.organization == target
        assert summary.memberships_moved == 1
        assert summary.memberships_discarded == 0

    def test_target_role_wins_when_the_user_is_in_both(self, source, target):
        user = UserFactory()
        MembershipFactory(organization=source, user=user, role="admin")
        MembershipFactory(organization=target, user=user, role="viewer")

        summary = merge_organizations(source, target)

        membership = UserOrganizationMembership.objects.get(user=user)
        assert membership.organization == target
        assert membership.role == "viewer"
        assert summary.memberships_moved == 0
        assert summary.memberships_discarded == 1


class TestPendingInvites:
    def test_pending_invite_moves(self, source, target):
        invite = OrganizationInviteFactory(organization=source, email="new@example.com")

        summary = merge_organizations(source, target)

        invite.refresh_from_db()
        assert invite.organization == target
        assert summary.invites_moved == 1

    @pytest.mark.parametrize("status", [OrganizationInvite.Status.ACCEPTED, OrganizationInvite.Status.REVOKED])
    def test_invite_that_is_not_pending_is_discarded(self, source, target, status):
        invite = OrganizationInviteFactory(organization=source, status=status)

        summary = merge_organizations(source, target)

        # The row check alone cannot fail: OrganizationInvite.organization is CASCADE,
        # so source.delete() removes it either way. The counters are what discriminate.
        assert (summary.invites_moved, summary.invites_discarded) == (0, 1)
        assert not OrganizationInvite.objects.filter(pk=invite.pk).exists()

    def test_expired_invite_is_discarded(self, source, target):
        invite = OrganizationInviteFactory(organization=source)
        stale = timezone.now() - timedelta(days=OrganizationInvite.EXPIRY_DAYS + 1)
        # date_modified is auto_now, so bypass save() to age the row.
        OrganizationInvite.objects.filter(pk=invite.pk).update(date_modified=stale)

        summary = merge_organizations(source, target)

        assert (summary.invites_moved, summary.invites_discarded) == (0, 1)
        assert not OrganizationInvite.objects.filter(pk=invite.pk).exists()

    def test_invite_colliding_with_a_target_invite_is_discarded(self, source, target):
        source_invite = OrganizationInviteFactory(organization=source, email="dup@example.com")
        target_invite = OrganizationInviteFactory(organization=target, email="dup@example.com")

        summary = merge_organizations(source, target)

        assert not OrganizationInvite.objects.filter(pk=source_invite.pk).exists()
        assert OrganizationInvite.objects.filter(pk=target_invite.pk).exists()
        assert summary.invites_discarded == 1

    def test_invite_for_an_existing_target_member_is_discarded(self, source, target):
        member = UserFactory(email="member@example.com")
        MembershipFactory(organization=target, user=member, role="admin")
        invite = OrganizationInviteFactory(organization=source, email="member@example.com")

        summary = merge_organizations(source, target)

        assert (summary.invites_moved, summary.invites_discarded) == (0, 1)
        assert not OrganizationInvite.objects.filter(pk=invite.pk).exists()
        assert UserOrganizationMembership.objects.get(user=member).role == "admin"

    def test_invite_for_a_member_moved_by_this_merge_is_discarded(self, source, target):
        """Proves invites are handled after memberships, not before."""
        member = UserFactory(email="mover@example.com")
        MembershipFactory(organization=source, user=member, role="member")
        OrganizationInviteFactory(organization=source, email="mover@example.com")

        summary = merge_organizations(source, target)

        # Discriminates against two distinct bugs: a no-op helper (counts stay 0, 0),
        # and the helpers being called in the wrong order -- invites before memberships
        # would not yet see the moved membership, so the invite would be MOVED (1, 0)
        # rather than discarded.
        assert (summary.invites_moved, summary.invites_discarded) == (0, 1)
        assert not OrganizationInvite.objects.filter(email="mover@example.com").exists()
        assert UserOrganizationMembership.objects.get(user=member).organization == target
