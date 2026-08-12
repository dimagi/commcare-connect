import pytest

from commcare_connect.opportunity.models import LabsRecord
from commcare_connect.opportunity.tests.factories import CommCareAppFactory, OpportunityFactory, PaymentFactory
from commcare_connect.organization.merge import SIMPLE_REASSIGNMENTS, MergeNotAllowed, merge_organizations
from commcare_connect.organization.models import Organization
from commcare_connect.program.tests.factories import ProgramFactory
from commcare_connect.users.tests.factories import OrganizationFactory

pytestmark = pytest.mark.django_db


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
