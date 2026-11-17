from http import HTTPStatus

import pytest
from django.urls import reverse
from waffle.testutils import override_switch

from commcare_connect.flags.switch_names import SOLICITATIONS
from commcare_connect.program.tests.factories import ProgramFactory
from commcare_connect.solicitation.filters import SolicitationDirectoryFilterSet
from commcare_connect.solicitation.models import Solicitation
from commcare_connect.solicitation.tables import SolicitationDirectoryTable
from commcare_connect.solicitation.tests.factories import SolicitationFactory
from commcare_connect.users.tests.factories import OrganizationFactory

Status = Solicitation.Status


def _filtered_solicitations(params):
    return set(SolicitationDirectoryFilterSet(params, queryset=Solicitation.objects.all()).qs)


@pytest.fixture
def directory_client(client, user_with_directory_access):
    client.force_login(user_with_directory_access)
    with override_switch(SOLICITATIONS, active=True):
        yield client


@pytest.mark.django_db
class TestSolicitationDirectoryFilterSet:
    @pytest.mark.parametrize("search", ["malaria", "umoja", "outreach"])
    def test_search_matches_title_organization_and_program(self, search):
        solicitation = SolicitationFactory(
            title="Malaria pilot",
            organization__name="Umoja Health Trust",
            program=ProgramFactory(name="Outreach"),
        )
        SolicitationFactory(title="Unrelated")

        assert _filtered_solicitations({"search": search}) == {solicitation}

    @pytest.mark.parametrize(
        "status, deadline_passed, shown_as",
        [
            (Status.DRAFT, False, Status.DRAFT),
            (Status.ACTIVE, False, Status.ACTIVE),
            # Past its deadline but not yet closed by update_solicitation_statuses.
            (Status.ACTIVE, True, Status.CLOSED),
            (Status.CLOSED, False, Status.CLOSED),
            (Status.CANCELLED, False, Status.CANCELLED),
        ],
    )
    def test_status_filter_matches_the_status_shown(self, status, deadline_passed, shown_as):
        solicitation = SolicitationFactory(status=status, deadline_passed=deadline_passed)

        assert {status for status in Status if solicitation in _filtered_solicitations({"status": [status]})} == {
            shown_as
        }

    def test_organization_filter_lists_program_managers_by_name(self):
        program_manager = OrganizationFactory(name="Umoja Health Trust", program_manager=True)
        OrganizationFactory(program_manager=False)

        choices = SolicitationDirectoryFilterSet({}, queryset=Solicitation.objects.all()).form["organization"]
        assert [(choice.data["value"].value, choice.data["label"]) for choice in choices] == [
            (program_manager.pk, "Umoja Health Trust")
        ]

    def test_test_organizations_solicitations_are_listed_only_when_asked_for(self):
        real = SolicitationFactory()
        test = SolicitationFactory(organization__is_test=True)

        assert _filtered_solicitations({}) == {real}
        assert _filtered_solicitations({"organization_test": "on"}) == {test}


@pytest.mark.django_db
@pytest.mark.parametrize(
    "status, deadline_passed, url_names",
    [
        (Status.DRAFT, False, {"details"}),
        (Status.ACTIVE, False, {"details", "invitations"}),
        (Status.ACTIVE, True, set()),
        (Status.CLOSED, False, set()),
        (Status.CANCELLED, False, set()),
    ],
)
def test_row_actions_link_to_the_solicitations_organization(status, deadline_passed, url_names):
    solicitation = SolicitationFactory(status=status, deadline_passed=deadline_passed)
    actions = SolicitationDirectoryTable([solicitation]).rows[0].get_cell("actions")

    args = (solicitation.organization.slug, solicitation.solicitation_id)
    linked = {name for name in ("details", "invitations") if reverse(f"solicitation:{name}", args=args) in actions}
    assert linked == url_names


@pytest.mark.django_db
class TestSolicitationDirectoryListView:
    def test_lists_every_organizations_solicitations(self, directory_client):
        solicitations = SolicitationFactory.create_batch(2)

        response = directory_client.get(reverse("organization_directory:solicitations"))

        assert response.status_code == HTTPStatus.OK
        assert set(response.context["table"].data) == set(solicitations)
        assert response.context["solicitations_count"] == 2

    @pytest.mark.parametrize("switch_active", [True, False])
    def test_tab_shows_only_while_the_switch_is_on(self, client, user_with_directory_access, switch_active):
        client.force_login(user_with_directory_access)

        with override_switch(SOLICITATIONS, active=switch_active):
            response = client.get(reverse("organization_directory:list"))

        assert (reverse("organization_directory:solicitations") in response.content.decode()) == switch_active
