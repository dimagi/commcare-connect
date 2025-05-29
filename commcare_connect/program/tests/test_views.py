from http import HTTPStatus

import pytest
from django.contrib import messages
from django.http import HttpResponseRedirect
from django.test import Client
from django.urls import reverse

from commcare_connect.opportunity.tests.factories import DeliveryTypeFactory
from commcare_connect.organization.models import Organization
from commcare_connect.program.models import Program, ProgramApplication, ProgramApplicationStatus
from commcare_connect.program.tests.factories import ProgramApplicationFactory, ProgramFactory
from commcare_connect.users.models import User
from commcare_connect.users.tests.factories import OrganizationFactory


class BaseProgramTest:
    @pytest.fixture(autouse=True)
    def base_setup(self, program_manager_org: Organization, program_manager_org_user_admin: User, client: Client):
        self.organization = program_manager_org
        self.user = program_manager_org_user_admin
        self.client = client
        client.force_login(self.user)
        self.list_url = reverse("program:home", kwargs={"org_slug": self.organization.slug})


@pytest.mark.django_db
class TestProgramCreateOrUpdateView(BaseProgramTest):
    @pytest.fixture(autouse=True)
    def test_setup(self):
        self.program = ProgramFactory.create(organization=self.organization)
        self.delivery_type = DeliveryTypeFactory.create()
        self.init_url = reverse("program:init", kwargs={"org_slug": self.organization.slug})
        self.edit_url = reverse("program:edit", kwargs={"org_slug": self.organization.slug, "pk": self.program.pk})

    def test_create_view(self):
        response = self.client.get(self.init_url)
        assert response.status_code == HTTPStatus.OK
        assert "program/program_form.html" in response.templates[0].name

    def test_create_program(self):
        data = {
            "name": "New Program",
            "description": "A description for the new program",
            "delivery_type": self.delivery_type.id,
            "budget": 10000,
            "currency": "USD",
            "start_date": "2024-01-01",
            "end_date": "2024-12-31",
        }
        response = self.client.post(self.init_url, data)
        assert response.status_code == HTTPStatus.FOUND
        new_program = Program.objects.get(name="New Program")
        assert new_program.name == "New Program"
        assert new_program.organization.slug == self.organization.slug
        assert "Program 'New Program' created successfully." in [
            msg.message for msg in messages.get_messages(response.wsgi_request)
        ]
        assert response.url == self.list_url

    def test_update_view(self):
        response = self.client.get(self.edit_url)
        assert response.status_code == HTTPStatus.OK
        assert "program/program_form.html" in response.templates[0].name

    def test_update_program(self):
        data = {
            "name": "Updated Program Name",
            "description": "Updated description",
            "delivery_type": self.delivery_type.id,
            "organization": self.organization.id,
            "budget": 15000,
            "currency": "EUR",
            "start_date": "2024-02-01",
            "end_date": "2024-11-30",
        }
        response = self.client.post(self.edit_url, data)
        assert response.status_code == HTTPStatus.FOUND
        old_org = self.program.organization.slug
        self.program.refresh_from_db()
        assert self.program.name == "Updated Program Name"
        assert self.program.organization.slug == old_org
        assert "Program 'Updated Program Name' updated successfully." in [
            msg.message for msg in messages.get_messages(response.wsgi_request)
        ]
        assert response.url == self.list_url


@pytest.mark.django_db
class TestInviteOrganizationView(BaseProgramTest):
    @pytest.fixture(autouse=True)
    def test_setup(self, organization: Organization):
        self.invite_organization = organization
        self.program = ProgramFactory.create(organization=self.organization)
        self.valid_url = reverse(
            "program:invite_organization",
            kwargs={
                "org_slug": self.organization.slug,
                "pk": self.program.pk,
            },
        )

    def test_successful_invitation(self):
        data = {
            "organization": self.invite_organization.slug,
        }
        response = self.client.post(self.valid_url, data)
        assert response.status_code == HttpResponseRedirect.status_code
        assert ProgramApplication.objects.filter(
            program=self.program,
            organization=self.invite_organization,
            status=ProgramApplicationStatus.INVITED,
        ).exists()
        assert "Organization invited successfully!" in [
            msg.message for msg in messages.get_messages(response.wsgi_request)
        ]

    def test_invalid_organization_slug(self):
        data = {
            "organization": "invalid_slug",
        }
        response = self.client.post(self.valid_url, data)
        assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.django_db
class TestManagedOpportunityApplicationListView(BaseProgramTest):
    @pytest.fixture(autouse=True)
    def test_setup(self, organization: Organization):
        self.program = ProgramFactory.create(organization=self.organization)
        self.applications = ProgramApplicationFactory.create_batch(20, program=self.program)
        self.list_url = reverse(
            "program:applications",
            kwargs={
                "org_slug": self.organization.slug,
                "pk": self.program.pk,
            },
        )

    def test_view_url_exists_at_desired_location(self):
        response = self.client.get(self.list_url)
        assert response.status_code == HTTPStatus.OK
        assert "program/application_list.html" in response.templates[0].name
        context = response.context_data
        assert "object_list" in context
        assert "pk" in context
        assert "program" in context
        assert "organizations" in context
        assert context["pk"] == self.program.pk

    def test_list_applications(self):
        response = self.client.get(self.list_url)
        assert response.status_code == HTTPStatus.OK
        applications = response.context_data["object_list"]
        assert len(applications) == 10

    def test_pagination(self):
        response = self.client.get(f"{self.list_url}?page=2")
        assert response.status_code == HTTPStatus.OK
        assert len(response.context_data["object_list"]) == 10


@pytest.mark.django_db
class TestManageApplicationView(BaseProgramTest):
    @pytest.fixture(autouse=True)
    def test_setup(self):
        self.invited_org = OrganizationFactory()
        self.program = ProgramFactory.create(organization=self.organization)
        self.application = ProgramApplicationFactory.create(
            organization=self.invited_org, program=self.program, status=ProgramApplicationStatus.APPLIED
        )
        self.application_list_url = reverse(
            "program:applications",
            kwargs={
                "org_slug": self.organization.slug,
                "pk": self.program.id,
            },
        )

    def test_accept_application(self):
        url = reverse(
            "program:manage_application",
            kwargs={
                "org_slug": self.organization.slug,
                "application_id": self.application.id,
                "action": "accept",
            },
        )
        response = self.client.post(url)
        assert response.status_code == HTTPStatus.FOUND
        self.application.refresh_from_db()
        assert self.application.status == ProgramApplicationStatus.ACCEPTED

    def test_reject_application(self):
        url = reverse(
            "program:manage_application",
            kwargs={
                "org_slug": self.organization.slug,
                "application_id": self.application.id,
                "action": "reject",
            },
        )
        response = self.client.post(url)
        assert response.status_code == HTTPStatus.FOUND
        self.application.refresh_from_db()
        assert self.application.status == ProgramApplicationStatus.REJECTED

    def test_invalid_action(self):
        url = reverse(
            "program:manage_application",
            kwargs={
                "org_slug": self.organization.slug,
                "application_id": self.application.id,
                "action": "invite",
            },
        )
        response = self.client.post(url)
        assert response.status_code == HTTPStatus.FOUND
        assert self.application.status == ProgramApplicationStatus.APPLIED
