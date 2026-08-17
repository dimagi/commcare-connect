import pytest
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.urls import reverse

from commcare_connect.commcarehq.tests.factories import HQServerFactory
from commcare_connect.flags.tests.factories import FlagFactory
from commcare_connect.opportunity.tests.factories import CommCareAppFactory, OpportunityFactory
from commcare_connect.organization.admin import MERGE_ACTION, OrganizationMergeForm, merge_preview
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


def _selected(*organizations):
    return Organization.objects.filter(pk__in=[org.pk for org in organizations])


class TestOrganizationMergeForm:
    def test_source_is_whichever_selected_organization_was_not_chosen(self, source, target):
        form = OrganizationMergeForm(data={"target": target.pk}, selected=_selected(source, target))

        assert form.is_valid(), form.errors
        assert form.source_and_target() == (source, target)

    def test_target_outside_the_selection_is_rejected(self, source, target):
        """A forged POST must not retarget an organization the admin never reviewed."""
        unreviewed = OrganizationFactory(name="Unreviewed Workspace")

        form = OrganizationMergeForm(data={"target": unreviewed.pk}, selected=_selected(source, target))

        assert not form.is_valid()
        assert "target" in form.errors


class TestMergePreview:
    def test_profile_rows_pair_each_field_across_both_workspaces(self, source, target):
        preview = merge_preview([source, target])

        assert ("Name", [source.name, target.name]) in preview["profile_rows"]

    def test_count_rows_pair_each_relation_across_both_workspaces(self, source, target):
        OpportunityFactory(organization=source)

        preview = merge_preview([source, target])

        assert ("opportunity.Opportunity.organization", [1, 0]) in preview["count_rows"]

    def test_flag_names_are_listed_because_flags_are_never_transferred(self, source, target):
        flag = FlagFactory(name="a-source-only-flag")
        flag.organizations.add(source)

        preview = merge_preview([source, target])

        assert preview["flag_names"] == [["a-source-only-flag"], []]

    def test_hidden_programs_are_listed_against_the_workspace_that_would_survive(self, source, target):
        ProgramFactory(organization=source, name="Zinc Supplementation")

        preview = merge_preview([source, target])

        assert preview["hidden_programs"] == [[], ["Zinc Supplementation"]]

    def test_a_program_manager_survivor_hides_nothing(self, source):
        target = OrganizationFactory(name="Target Workspace", program_manager=True)
        ProgramFactory(organization=source, name="Zinc Supplementation")

        preview = merge_preview([source, target])

        assert preview["hidden_programs"] == [[], []]


def _run_action(client, organizations, **extra):
    return client.post(
        reverse("admin:organization_organization_changelist"),
        data={
            "action": MERGE_ACTION,
            ACTION_CHECKBOX_NAME: [organization.pk for organization in organizations],
            **extra,
        },
    )


class TestMergeActionConfirmation:
    def test_confirmation_page_shows_both_workspaces_and_merges_nothing_yet(self, admin_client, source, target):
        response = _run_action(admin_client, [source, target])

        content = response.content.decode()
        assert response.status_code == 200
        assert source.name in content
        assert target.name in content
        assert Organization.objects.filter(pk__in=[source.pk, target.pk]).count() == 2

    @pytest.mark.parametrize("selection_size", [1, 3])
    def test_a_selection_that_is_not_a_pair_is_refused(self, admin_client, selection_size):
        organizations = [OrganizationFactory() for _ in range(selection_size)]

        response = _run_action(admin_client, organizations)

        assert response.status_code == 302
        assert Organization.objects.filter(pk__in=[o.pk for o in organizations]).count() == selection_size


class TestMergeActionExecution:
    def test_confirming_merges_the_source_into_the_chosen_target(self, admin_client, source, target):
        opportunity = OpportunityFactory(organization=source)

        response = _run_action(admin_client, [source, target], confirm="yes", target=target.pk)

        assert response.status_code == 302
        assert not Organization.objects.filter(pk=source.pk).exists()
        opportunity.refresh_from_db()
        assert opportunity.organization == target

    def test_a_refused_merge_reports_the_reason_and_changes_nothing(self, admin_client, source, target):
        shared = dict(cc_app_id="shared-app", cc_domain="shared-domain", hq_server=HQServerFactory())
        CommCareAppFactory(organization=source, **shared)
        CommCareAppFactory(organization=target, **shared)

        response = _run_action(admin_client, [source, target], confirm="yes", target=target.pk)

        assert response.status_code == 302
        assert Organization.objects.filter(pk__in=[source.pk, target.pk]).count() == 2
        messages = [str(m) for m in response.wsgi_request._messages]
        assert any("shared-domain/shared-app" in message for message in messages)
