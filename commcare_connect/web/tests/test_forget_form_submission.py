import pytest
from django.template.loader import render_to_string


@pytest.mark.django_db
@pytest.mark.parametrize("method, expected", [("get", False), ("post", True)])
def test_form_submission_forgotten_only_on_post_responses(rf, user, method, expected):
    # Pages answering a POST replace their history entry, so Back/reload under no-store don't ask to resubmit
    request = getattr(rf, method)("/a/org/organization/")
    request.user = user

    html = render_to_string("components/forget_form_submission.html", request=request)

    assert ("history.replaceState" in html) is expected
