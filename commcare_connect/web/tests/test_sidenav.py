import pytest
from django.urls import resolve

from commcare_connect.web.templatetags.sidenav import sidenav_group_open


@pytest.mark.parametrize(
    "path, expected",
    [("/solicitations/", "true"), ("/users/internal_features/", "false")],
)
def test_sidenav_group_open_when_the_page_is_in_one_of_its_namespaces(rf, path, expected):
    request = rf.get(path)
    request.resolver_match = resolve(path)

    assert sidenav_group_open({"request": request}, "solicitation", "marketplace") == expected


def test_sidenav_group_closed_without_a_resolved_url(rf):
    assert sidenav_group_open({"request": rf.get("/")}, "marketplace") == "false"
