from unittest import mock

import pytest
from django.contrib import messages
from django.contrib.messages.middleware import MessageMiddleware
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import HttpRequest
from django.test import RequestFactory

from commcare_connect.organization.models import Organization
from commcare_connect.users.forms import UserAdminChangeForm
from commcare_connect.users.models import ConnectIDUserLink, User
from commcare_connect.users.views import UserRedirectView, UserUpdateView, create_user_link_view

pytestmark = pytest.mark.django_db


class TestUserUpdateView:
    """
    TODO:
        extracting view initialization code as class-scoped fixture
        would be great if only pytest-django supported non-function-scoped
        fixture db access -- this is a work-in-progress for now:
        https://github.com/pytest-dev/pytest-django/pull/258
    """

    def dummy_get_response(self, request: HttpRequest):
        return None

    def test_get_success_url(self, user: User, rf: RequestFactory):
        view = UserUpdateView()
        request = rf.get("/fake-url/")
        request.user = user

        view.request = request
        assert view.get_success_url() == "/accounts/email/"

    def test_get_object(self, user: User, rf: RequestFactory):
        view = UserUpdateView()
        request = rf.get("/fake-url/")
        request.user = user

        view.request = request

        assert view.get_object() == user

    def test_form_valid(self, user: User, rf: RequestFactory):
        view = UserUpdateView()
        request = rf.get("/fake-url/")

        # Add the session/message middleware to the request
        SessionMiddleware(self.dummy_get_response).process_request(request)
        MessageMiddleware(self.dummy_get_response).process_request(request)
        request.user = user

        view.request = request

        # Initialize the form
        form = UserAdminChangeForm()
        form.cleaned_data = {}
        form.instance = user
        view.form_valid(form)

        messages_sent = [m.message for m in messages.get_messages(request)]
        assert messages_sent == ["Information successfully updated"]


class TestUserRedirectView:
    def test_get_redirect_url(self, user: User, rf: RequestFactory):
        view = UserRedirectView()
        request = rf.get("/fake-url")
        request.user = user
        request.org = None

        view.request = request
        assert view.get_redirect_url() == "/register/organization/"

    def test_get_redirect_url_for_org_user(
        self, organization: Organization, org_user_member: User, rf: RequestFactory
    ):
        view = UserRedirectView()
        request = rf.get("/fake-url")
        request.user = org_user_member
        request.org = organization

        view.request = request
        assert view.get_redirect_url() == f"/a/{organization.slug}/opportunity/"


class TestCreateUserLinkView:
    def test_view(self, mobile_user: User, rf: RequestFactory):
        request = rf.post("/fake-url/", data={"commcare_username": "abc", "connect_username": mobile_user.username})
        request.user = mobile_user
        with mock.patch(
            "oauth2_provider.views.mixins.ClientProtectedResourceMixin.authenticate_client"
        ) as authenticate_client:
            authenticate_client.return_value = True
            response = create_user_link_view(request)
        user_link = ConnectIDUserLink.objects.get(user=mobile_user)
        assert response.status_code == 201
        assert user_link.commcare_username == "abc"
