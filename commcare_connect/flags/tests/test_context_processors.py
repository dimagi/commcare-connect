from __future__ import annotations

import pytest
from django.test import RequestFactory

from commcare_connect.flags.models import Flag
from commcare_connect.program.tests.factories import ManagedOpportunityFactory, ProgramFactory
from commcare_connect.users.tests.factories import MembershipFactory, OrganizationFactory, UserFactory
from commcare_connect.web.context_processors import chat_widget_context


@pytest.mark.django_db
class TestChatWidgetContext:
    def test_unauthenticated_user(self, settings):
        settings.CHATBOT_ID = "test-chatbot-id"
        settings.CHATBOT_EMBED_KEY = "test-embed-key"

        request = RequestFactory().get("/")
        request.user = type("Anon", (), {"is_authenticated": False})()
        context = chat_widget_context(request)
        assert context["chat_widget_enabled"] is False
        assert context["chatbot_id"] == "test-chatbot-id"
        assert context["chatbot_embed_key"] == "test-embed-key"

    def test_flag_missing(self, settings):
        settings.CHATBOT_ID = "test-chatbot-id"
        settings.CHATBOT_EMBED_KEY = "test-embed-key"

        request = RequestFactory().get("/")
        request.user = UserFactory()
        assert chat_widget_context(request)["chat_widget_enabled"] is False

    def test_flag_enabled_for_org(self, settings):
        settings.CHATBOT_ID = "test-chatbot-id"
        settings.CHATBOT_EMBED_KEY = "test-embed-key"

        org = OrganizationFactory()
        user = UserFactory()
        MembershipFactory(user=user, organization=org)
        flag = Flag.objects.create(name="open_chat_studio_widget")
        flag.organizations.add(org)

        request = RequestFactory().get("/")
        request.user = user
        request.org = org

        assert chat_widget_context(request)["chat_widget_enabled"] is True

    def test_flag_enabled_for_org_but_missing_creds(self, settings):
        settings.CHATBOT_ID = ""
        settings.CHATBOT_EMBED_KEY = ""

        org = OrganizationFactory()
        user = UserFactory()
        MembershipFactory(user=user, organization=org)
        flag = Flag.objects.create(name="open_chat_studio_widget")
        flag.organizations.add(org)

        request = RequestFactory().get("/")
        request.user = user
        request.org = org

        assert chat_widget_context(request)["chat_widget_enabled"] is False

    def test_flag_enabled_for_user(self, settings):
        settings.CHATBOT_ID = "test-chatbot-id"
        settings.CHATBOT_EMBED_KEY = "test-embed-key"

        user = UserFactory()
        flag = Flag.objects.create(name="open_chat_studio_widget")
        flag.users.add(user)

        request = RequestFactory().get("/")
        request.user = user

        context = chat_widget_context(request)
        assert context["chat_widget_enabled"] is True
        assert context["chatbot_id"] == "test-chatbot-id"
        assert context["chatbot_embed_key"] == "test-embed-key"

    def test_flag_enabled_for_program_of_managed_opportunity(self, settings, managed_opportunity):
        settings.CHATBOT_ID = "test-chatbot-id"
        settings.CHATBOT_EMBED_KEY = "test-embed-key"

        user = UserFactory()
        MembershipFactory(user=user, organization=managed_opportunity.organization)
        flag = Flag.objects.create(name="open_chat_studio_widget")
        flag.programs.add(managed_opportunity.program)

        request = RequestFactory().get("/")
        request.user = user
        request.org = managed_opportunity.organization
        request.opportunity = managed_opportunity

        assert chat_widget_context(request)["chat_widget_enabled"] is True

    def test_flag_does_not_leak_across_programs(self, settings, organization):
        settings.CHATBOT_ID = "test-chatbot-id"
        settings.CHATBOT_EMBED_KEY = "test-embed-key"

        program_with_flag = ProgramFactory()
        program_without_flag = ProgramFactory()
        ManagedOpportunityFactory(program=program_with_flag, organization=organization)
        opp_without_flag = ManagedOpportunityFactory(program=program_without_flag, organization=organization)

        user = UserFactory()
        MembershipFactory(user=user, organization=organization)

        flag = Flag.objects.create(name="open_chat_studio_widget")
        flag.programs.add(program_with_flag)

        request = RequestFactory().get("/")
        request.user = user
        request.org = organization
        request.opportunity = opp_without_flag

        assert chat_widget_context(request)["chat_widget_enabled"] is False

    def test_flag_does_not_leak_across_orgs_for_multi_org_user(self, settings):
        settings.CHATBOT_ID = "test-chatbot-id"
        settings.CHATBOT_EMBED_KEY = "test-embed-key"

        org_with_flag = OrganizationFactory()
        org_without_flag = OrganizationFactory()

        user = UserFactory()
        MembershipFactory(user=user, organization=org_with_flag)
        MembershipFactory(user=user, organization=org_without_flag)

        flag = Flag.objects.create(name="open_chat_studio_widget")
        flag.organizations.add(org_with_flag)

        request = RequestFactory().get("/")
        request.user = user
        request.org = org_without_flag

        assert chat_widget_context(request)["chat_widget_enabled"] is False
