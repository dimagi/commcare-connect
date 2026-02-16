from http import HTTPStatus

import pytest
from django.contrib import messages
from django.http import HttpResponseRedirect
from django.test import Client
from django.urls import reverse

from commcare_connect.opportunity.tests.factories import DeliveryTypeFactory
from commcare_connect.organization.models import Organization
from commcare_connect.program.models import Program, ProgramApplication, ProgramApplicationStatus
from commcare_connect.program.tests.factories import (
    ManagedOpportunityFactory,
    ProgramApplicationFactory,
    ProgramFactory,
)
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
        self.edit_url = reverse(
            "program:edit", kwargs={"org_slug": self.organization.slug, "pk": self.program.program_id}
        )

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
            "country": "USA",
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
            "currency": "INR",
            "country": "IND",
            "start_date": "2024-02-01",
            "end_date": "2024-11-30",
        }
        response = self.client.post(self.edit_url, data)
        assert response.status_code == HTTPStatus.FOUND
        old_org = self.program.organization.slug
        self.program.refresh_from_db()
        assert self.program.name == "Updated Program Name"
        assert self.program.organization.slug == old_org
        assert self.program.currency_id == data["currency"]
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
                "pk": self.program.program_id,
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
class TestProgramHomeBudgetData(BaseProgramTest):
    @pytest.fixture(autouse=True)
    def setup_program(self):
        self.program = ProgramFactory.create(organization=self.organization, budget=10000)
        self.other_program = ProgramFactory.create(organization=self.organization, budget=15000)

        application_orgs = OrganizationFactory.create_batch(2)
        self.expected_application_budgets = {}
        self.program_applications = []

        budgets_per_org = {
            application_orgs[0]: [250, 150],
            application_orgs[1]: [400],
        }
        for org, budgets in budgets_per_org.items():
            application = ProgramApplicationFactory.create(
                program=self.program,
                organization=org,
                status=ProgramApplicationStatus.ACCEPTED,
            )
            self.program_applications.append(application)
            self.expected_application_budgets[org.id] = sum(budgets)
            for amount in budgets:
                ManagedOpportunityFactory.create(program=self.program, organization=org, total_budget=amount)

        # Application without any managed opportunities should show zero budget
        empty_org = OrganizationFactory()
        empty_application = ProgramApplicationFactory.create(program=self.program, organization=empty_org)
        self.program_applications.append(empty_application)
        self.expected_application_budgets[empty_org.id] = 0

        # Managed opportunity for a different program should be ignored
        ManagedOpportunityFactory.create(
            program=self.other_program,
            organization=application_orgs[0],
            total_budget=999,
        )
        # Managed opportunity for an org without an application should be ignored
        ManagedOpportunityFactory.create(program=self.program, organization=OrganizationFactory(), total_budget=777)

        self.expected_allocated_budget = sum(self.expected_application_budgets.values())

    def test_program_home_includes_budget_data(self):
        response = self.client.get(self.list_url)
        assert response.status_code == HTTPStatus.OK
        programs = response.context["programs"]
        program = next((p for p in programs if p.id == self.program.id), None)
        assert program is not None
        assert program.allocated_budget == self.expected_allocated_budget

        applications = getattr(program, "applications_with_budget", [])
        assert len(applications) == len(self.expected_application_budgets)
        for application in applications:
            expected_budget = self.expected_application_budgets[application.organization_id]
            assert application.current_budget == expected_budget
