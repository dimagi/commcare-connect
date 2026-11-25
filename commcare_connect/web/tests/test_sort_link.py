from urllib.parse import parse_qs, urlparse

import pytest
from django.utils.html import escape

from commcare_connect.web.templatetags.sort_link import sortable_header


def _sort_url_params(request, **kwargs):
    html = sortable_header({"request": request}, "name", "Name", **kwargs)
    href = html.split('href="')[1].split('"')[0].replace(escape("&"), "&")
    return parse_qs(urlparse(href).query)


@pytest.mark.parametrize("use_view_url", [True, False])
def test_sorting_keeps_every_value_of_a_multi_value_param(rf, use_view_url):
    url = "/list/?delivery_type=1&delivery_type=2&status=0"
    request = rf.get(url, headers={"referer": f"http://testserver{url}"})

    params = _sort_url_params(request, use_view_url=use_view_url)

    assert params == {"delivery_type": ["1", "2"], "status": ["0"], "sort": ["name"]}


@pytest.mark.parametrize("use_view_url", [True, False])
@pytest.mark.parametrize("current, expected", [("", ["name"]), ("name", ["-name"]), ("-name", None)])
def test_sorting_cycles_ascending_descending_unsorted(rf, use_view_url, current, expected):
    url = f"/list/?sort={current}" if current else "/list/"
    request = rf.get(url, headers={"referer": f"http://testserver{url}"})

    assert _sort_url_params(request, use_view_url=use_view_url).get("sort") == expected
