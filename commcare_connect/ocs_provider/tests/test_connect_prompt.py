import pytest
from django.template.loader import render_to_string


@pytest.mark.django_db
def test_connect_prompt_renders_ocs_connect_link(rf, user):
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
