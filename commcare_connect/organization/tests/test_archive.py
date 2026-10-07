from datetime import timedelta
from urllib.parse import urlencode

import pytest
from django.contrib.auth.models import Permission
from django.urls import reverse
from django.utils import timezone

from commcare_connect.organization.archive import ArchiveNotAllowed, archive_organization, has_live_program
from commcare_connect.organization.models import Organization, OrganizationStatus
from commcare_connect.program.models import ProgramApplicationStatus
from commcare_connect.program.tests.factories import ProgramApplicationFactory, ProgramFactory
from commcare_connect.users.tests.factories import OrganizationFactory, UserFactory

pytestmark = pytest.mark.django_db

TODAY = timezone.now().date()


def _program_involving(organization, relation, end_date):
    if relation == "runs":
        return ProgramFactory(organization=organization, end_date=end_date)
    if relation == "funds":
        return ProgramFactory(funder=organization, end_date=end_date)
    program = ProgramFactory(end_date=end_date)
    if relation == "delivers":
        ProgramApplicationFactory(program=program, organization=organization, status=ProgramApplicationStatus.ACCEPTED)
    elif relation == "applied":
        ProgramApplicationFactory(program=program, organization=organization, status=ProgramApplicationStatus.APPLIED)
    elif relation == "watches":
        program.watchers.add(organization)
    return program


def _has_live_program(organization):
    return Organization.objects.annotate(live=has_live_program()).get(pk=organization.pk).live


class TestArchiveOrganization:
    @pytest.mark.parametrize("relation", ["runs", "funds", "delivers"])
    @pytest.mark.parametrize("end_date", [TODAY, TODAY + timedelta(days=30)])
    def test_refused_while_a_program_it_is_part_of_has_not_ended(self, relation, end_date):
        organization = OrganizationFactory(status=OrganizationStatus.ACTIVE)
        program = _program_involving(organization, relation, end_date)

        with pytest.raises(ArchiveNotAllowed, match=program.name):
            archive_organization(organization)

        organization.refresh_from_db()
        assert organization.status == OrganizationStatus.ACTIVE
        assert _has_live_program(organization)

    @pytest.mark.parametrize(
        "relation, end_date",
        [
            ("runs", TODAY - timedelta(days=1)),
            ("funds", TODAY - timedelta(days=1)),
            ("delivers", TODAY - timedelta(days=1)),
            ("applied", TODAY + timedelta(days=30)),
            ("watches", TODAY + timedelta(days=30)),
        ],
    )
    def test_archives_when_no_live_program_blocks_it(self, relation, end_date):
        organization = OrganizationFactory(status=OrganizationStatus.ACTIVE)
        _program_involving(organization, relation, end_date)

        archive_organization(organization)

        organization.refresh_from_db()
        assert organization.status == OrganizationStatus.ARCHIVED
        assert not _has_live_program(organization)


class TestOrganizationArchiveView:
    @pytest.fixture
    def staff_client(self, client):
        user = UserFactory()
        user.user_permissions.add(Permission.objects.get(codename="workspace_entity_management_access"))
        client.force_login(user)
        return client

    def _archive_url(self, organization, next_url=""):
        url = reverse("organization_directory:archive", args=(organization.slug,))
        return f"{url}?{urlencode({'next': next_url})}" if next_url else url

    def test_archives_and_returns_to_the_listing(self, staff_client):
        organization = OrganizationFactory()
        next_url = f"{reverse('organization_directory:list')}?search=x"

        response = staff_client.post(self._archive_url(organization, next_url))

        organization.refresh_from_db()
        assert organization.status == OrganizationStatus.ARCHIVED
        assert response.url == next_url

    def test_reports_why_it_was_refused(self, staff_client):
        organization = OrganizationFactory(status=OrganizationStatus.ACTIVE)
        program = ProgramFactory(organization=organization, end_date=TODAY)

        response = staff_client.post(self._archive_url(organization), follow=True)

        organization.refresh_from_db()
        assert organization.status == OrganizationStatus.ACTIVE
        assert program.name in str(list(response.context["messages"])[0])

    def test_get_is_not_allowed(self, staff_client):
        organization = OrganizationFactory()

        assert staff_client.get(self._archive_url(organization)).status_code == 405

    def test_requires_permission(self, client, user):
        organization = OrganizationFactory()
        client.force_login(user)

        assert client.post(self._archive_url(organization)).status_code == 403
