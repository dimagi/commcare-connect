import json
from unittest import mock
from unittest.mock import patch

import pytest
from django.contrib import messages
from django.contrib.auth.models import Permission
from django.contrib.messages.middleware import MessageMiddleware
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import HttpRequest
from django.test import RequestFactory
from django.urls import reverse

from commcare_connect.flags.tests.factories import SwitchFactory
from commcare_connect.opportunity.tests.factories import OpportunityAccessFactory, UserInviteFactory
from commcare_connect.organization.models import Organization
from commcare_connect.users.forms import UserAdminChangeForm
from commcare_connect.users.models import ConnectIDUserLink, User
from commcare_connect.users.views import UserRedirectView, UserToggleView, UserUpdateView, create_user_link_view
from commcare_connect.utils.error_codes import ErrorCodes

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
        assert view.get_redirect_url() == "/"

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


class TestRetrieveUserOTPView:
    @property
    def url(self):
        return reverse("users:connect_user_otp")

    def test_non_superuser_cannot_access_page(self, user, client):
        assert not user.is_superuser

        client.force_login(user)
        response = client.get(self.url)

        assert response.status_code == 403

    @patch("commcare_connect.users.views.get_user_otp")
    def test_can_get_user_otp(self, get_user_otp_mock, user, client):
        get_user_otp_mock.return_value = "1234"
        response = self._get_response(client, user)

        messages = list(response.context["messages"])
        assert str(messages[0]) == "The user's OTP is: 1234"

    @patch("commcare_connect.users.views.get_user_otp")
    def test_no_otp_returned(self, get_user_otp_mock, user, client):
        get_user_otp_mock.return_value = None
        response = self._get_response(client, user)

        expected_failure_message = (
            "Failed to fetch OTP. Please make sure the number is correct "
            "and that the user has started their device seating process."
        )

        messages = list(response.context["messages"])
        assert str(messages[0]) == expected_failure_message

    def _get_response(self, client, user):
        perm = Permission.objects.get(codename="otp_access")
        user.user_permissions.add(perm)

        client.force_login(user)
        return client.post(self.url, data={"phone_number": "+1234567890"}, follow=True)


@pytest.mark.django_db
class TestStartLearnAppView:
    @property
    def url(self):
        return reverse("users:start_learn_app")

    def _post(self, client, user, data, create_user_result=True):
        client.force_authenticate(user)
        with patch(
            "commcare_connect.users.views.create_hq_user_and_link", return_value=create_user_result
        ) as mock_create:
            response = client.post(self.url, data=data)
        return response, mock_create

    @pytest.mark.parametrize(
        "data, create_user_result, setup_access, expected_error",
        [
            # Case 1: Missing opportunity
            ({}, True, False, ErrorCodes.OPPORTUNITY_REQUIRED),
            # Case 2: HQ user creation fails
            (lambda opp: {"opportunity": opp.id}, False, False, ErrorCodes.FAILED_USER_CREATE),
            # Case 3: No access for the given opportunity
            (lambda opp: {"opportunity": opp.id}, True, False, ErrorCodes.NO_OPPORTUNITY_ACCESS),
        ],
    )
    def test_start_learning_errors(
        self,
        data,
        create_user_result,
        setup_access,
        expected_error,
        opportunity,
        user,
        api_client,
    ):
        if callable(data):
            data = data(opportunity)

        if setup_access:
            OpportunityAccessFactory(opportunity=opportunity, user=user)

        response, mock_create = self._post(
            api_client,
            user,
            data=data,
            create_user_result=create_user_result,
        )

        assert response.status_code == 400
        if expected_error == ErrorCodes.OPPORTUNITY_REQUIRED:
            mock_create.assert_not_called()
        else:
            mock_create.assert_called_once()
        assert response.json()["error_code"] == expected_error

    def test_starts_learning_successfully(self, opportunity, user, api_client):
        access = OpportunityAccessFactory(opportunity=opportunity, user=user)
        UserInviteFactory(opportunity=opportunity, opportunity_access=access)

        response, mock_create = self._post(
            api_client,
            user,
            data={"opportunity": opportunity.id},
            create_user_result=True,
        )
        assert response.status_code == 200
        mock_create.assert_called_once()


class TestUserToggleView:
    def test_no_toggles(self, mobile_user: User, rf: RequestFactory):
        user_toggle_view = UserToggleView.as_view()
        request = rf.get("/fake-url/", data={"username": "abc"})
        request.user = mobile_user
        with mock.patch(
            "oauth2_provider.views.mixins.ClientProtectedResourceMixin.authenticate_client"
        ) as authenticate_client:
            authenticate_client.return_value = True
            response = user_toggle_view(request)
        data = json.loads(response.content)

        assert response.status_code == 200
        assert "toggles" in data
        assert data["toggles"] == []

    def test_toggles(self, mobile_user: User, rf: RequestFactory):
        SwitchFactory(name="TEST_SWITCH")
        user_toggle_view = UserToggleView.as_view()
        request = rf.get("/fake-url/", data={"username": "abc"})
        request.user = mobile_user
        with mock.patch(
            "oauth2_provider.views.mixins.ClientProtectedResourceMixin.authenticate_client"
        ) as authenticate_client:
            authenticate_client.return_value = True
            response = user_toggle_view(request)
        data = json.loads(response.content)

        assert response.status_code == 200
        assert "toggles" in data
        toggle_response = data["toggles"][0]
        assert toggle_response["name"] == "TEST_SWITCH"
        assert toggle_response["active"] is True
        assert "created" in toggle_response
        assert "modified" in toggle_response
