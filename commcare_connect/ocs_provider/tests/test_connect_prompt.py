import pytest
from django.template.loader import render_to_string

from commcare_connect.users.tests.test_connections import _create_social_app


@pytest.mark.django_db
def test_connect_prompt_renders_ocs_connect_link(rf, user):
    _create_social_app("ocs")
    request = rf.get("/a/org/opportunity/tasks/")
    request.user = user

    html = render_to_string(
        "ocs/_connect_prompt.html",
        {"next_url": "/a/org/opportunity/tasks/"},
        request=request,
    )

    assert "/accounts/ocs/login/" in html
    assert "process=connect" in html
    assert "next=" in html
    assert "Connect" in html


@pytest.mark.django_db
def test_connect_prompt_without_a_social_app_says_ocs_is_unavailable(rf, user):
    request = rf.get("/a/org/opportunity/tasks/")
    request.user = user

    html = render_to_string(
        "ocs/_connect_prompt.html",
        {"next_url": "/a/org/opportunity/tasks/"},
        request=request,
    )

    assert "/accounts/ocs/login/" not in html
    assert "not configured" in html
