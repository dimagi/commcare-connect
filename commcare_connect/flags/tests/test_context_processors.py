from __future__ import annotations

import pytest
from django.test import RequestFactory

from commcare_connect.flags.models import Flag
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
