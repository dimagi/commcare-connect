import pytest
from allauth.socialaccount.models import SocialAccount

from commcare_connect.opportunity.views import _ocs_connect_context


@pytest.mark.django_db
def test_next_url_prefers_hx_current_url_over_path(rf, user):
    request = rf.get("/a/org/opportunity/create_task/", HTTP_HX_CURRENT_URL="/a/org/opportunity/tasks/")
    request.user = user

    context = _ocs_connect_context(request)

    assert context["ocs_next_url"] == "/a/org/opportunity/tasks/"


@pytest.mark.django_db
def test_next_url_falls_back_to_full_path(rf, user):
    request = rf.get("/a/org/opportunity/tasks/")
    request.user = user

    context = _ocs_connect_context(request)

    assert context["ocs_next_url"] == "/a/org/opportunity/tasks/"


@pytest.mark.django_db
def test_ocs_connected_reflects_social_account(rf, user):
    request = rf.get("/a/org/opportunity/tasks/")
    request.user = user

    assert _ocs_connect_context(request)["ocs_connected"] is False

    SocialAccount.objects.create(user=user, provider="ocs", uid="uid-ocs")

    assert _ocs_connect_context(request)["ocs_connected"] is True
