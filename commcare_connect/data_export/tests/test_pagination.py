from urllib.parse import parse_qs, urlparse

import pytest
from rest_framework.exceptions import ValidationError
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from commcare_connect.data_export.pagination import IdKeysetPagination
from commcare_connect.opportunity.models import UserVisit
from commcare_connect.opportunity.tests.factories import UserVisitFactory


@pytest.fixture
def paginator():
    return IdKeysetPagination()


@pytest.fixture
def api_rf():
    return APIRequestFactory()


def _make_request(api_rf, path="/export/test/", **params):
    wsgi_request = api_rf.get(path, data=params)
    return Request(wsgi_request)


def _collect_all_ids(api_rf, queryset, cursor_order="forward", page_size=2):
    """Walk all pages using IdKeysetPagination and return collected IDs."""
    all_ids = []
    params = {"page_size": page_size, "cursor_order": cursor_order}

    while True:
        paginator = IdKeysetPagination()
        request = _make_request(api_rf, **params)
        page = paginator.paginate_queryset(queryset, request)
        all_ids.extend(obj.id for obj in page)

        next_link = paginator.get_next_link()
        if next_link is None:
            break
        params = parse_qs(urlparse(next_link).query, keep_blank_values=True)
        params = {k: v[0] for k, v in params.items()}

    return all_ids


@pytest.mark.django_db
@pytest.mark.parametrize("cursor_order", ["forward", "reverse"])
class TestIdKeysetPagination:
    def test_first_page_no_cursor(self, paginator, api_rf, opportunity, org_user_member, cursor_order):
        visits = sorted(
            UserVisitFactory.create_batch(5, opportunity=opportunity, user=org_user_member),
            key=lambda v: v.id,
            reverse=cursor_order == "reverse",
        )
        request = _make_request(api_rf, page_size=3, cursor_order=cursor_order)
        queryset = UserVisit.objects.filter(opportunity=opportunity)

        page = paginator.paginate_queryset(queryset, request)
        response = paginator.get_paginated_response(page)

        assert len(page) == 3
        assert [obj.id for obj in page] == [v.id for v in visits[:3]]
        assert response.data["next"] is not None
        assert f"last_id={visits[2].id}" in response.data["next"]

    def test_last_id_returns_next_page(self, paginator, api_rf, opportunity, org_user_member, cursor_order):
        visits = sorted(
            UserVisitFactory.create_batch(5, opportunity=opportunity, user=org_user_member),
            key=lambda v: v.id,
            reverse=cursor_order == "reverse",
        )
        request = _make_request(api_rf, last_id=visits[2].id, page_size=3, cursor_order=cursor_order)
        queryset = UserVisit.objects.filter(opportunity=opportunity)

        page = paginator.paginate_queryset(queryset, request)

        assert len(page) == 2
        assert [obj.id for obj in page] == [v.id for v in visits[3:]]

    def test_pagination_traversal(self, api_rf, opportunity, org_user_member, cursor_order):
        visits = sorted(
            UserVisitFactory.create_batch(5, opportunity=opportunity, user=org_user_member),
            key=lambda v: v.id,
            reverse=cursor_order == "reverse",
        )
        queryset = UserVisit.objects.filter(opportunity=opportunity)

        all_ids = _collect_all_ids(api_rf, queryset, cursor_order=cursor_order, page_size=2)

        assert all_ids == [v.id for v in visits]
        assert len(set(all_ids)) == 5


@pytest.mark.django_db
class TestIdKeysetPaginationEdgeCases:
    def test_next_url_null_on_last_page(self, paginator, api_rf, opportunity, org_user_member):
        UserVisitFactory.create_batch(3, opportunity=opportunity, user=org_user_member)
        request = _make_request(api_rf, page_size=5)
        queryset = UserVisit.objects.filter(opportunity=opportunity)

        page = paginator.paginate_queryset(queryset, request)
        response = paginator.get_paginated_response(page)

        assert response.data["next"] is None

    def test_empty_queryset(self, paginator, api_rf, opportunity):
        request = _make_request(api_rf, page_size=10, cursor_order="forward")
        queryset = UserVisit.objects.filter(opportunity=opportunity)

        page = paginator.paginate_queryset(queryset, request)

        assert page == []

    @pytest.mark.parametrize(
        "page_size_param,expected_count,expected_page_size",
        [
            (99999, 3, 5000),  # clamped to max_page_size (5000)
            (None, 3, 1000),  # default page_size=1000 > 3 records
        ],
    )
    def test_page_size(
        self, paginator, api_rf, opportunity, org_user_member, page_size_param, expected_count, expected_page_size
    ):
        UserVisitFactory.create_batch(3, opportunity=opportunity, user=org_user_member)
        params = {"cursor_order": "forward"}
        if page_size_param is not None:
            params["page_size"] = page_size_param
        request = _make_request(api_rf, **params)
        queryset = UserVisit.objects.filter(opportunity=opportunity)

        page = paginator.paginate_queryset(queryset, request)

        assert len(page) == expected_count
        assert paginator.page_size == expected_page_size

    def test_default_cursor_order_is_forward(self, paginator, api_rf, opportunity, org_user_member):
        visits = sorted(
            UserVisitFactory.create_batch(3, opportunity=opportunity, user=org_user_member),
            key=lambda v: v.id,
        )
        request = _make_request(api_rf)
        queryset = UserVisit.objects.filter(opportunity=opportunity)

        page = paginator.paginate_queryset(queryset, request)

        assert [obj.id for obj in page] == [v.id for v in visits]

    def test_next_link_preserves_extra_query_params(self, paginator, api_rf, opportunity, org_user_member):
        UserVisitFactory.create_batch(3, opportunity=opportunity, user=org_user_member)
        request = _make_request(api_rf, page_size=2, images="true", custom="value")
        queryset = UserVisit.objects.filter(opportunity=opportunity)

        paginator.paginate_queryset(queryset, request)
        next_link = paginator.get_next_link()

        assert next_link is not None
        parsed = parse_qs(urlparse(next_link).query)
        assert parsed["images"] == ["true"]
        assert parsed["custom"] == ["value"]
        assert "last_id" in parsed
        assert "page_size" in parsed

    def test_next_link_preserves_multi_value_query_params(self, paginator, api_rf, opportunity, org_user_member):
        UserVisitFactory.create_batch(3, opportunity=opportunity, user=org_user_member)
        wsgi_request = api_rf.get("/export/test/", data={"page_size": 2, "filter": ["a", "b"]})
        request = Request(wsgi_request)
        queryset = UserVisit.objects.filter(opportunity=opportunity)

        paginator.paginate_queryset(queryset, request)
        next_link = paginator.get_next_link()

        assert next_link is not None
        parsed = parse_qs(urlparse(next_link).query)
        assert sorted(parsed["filter"]) == ["a", "b"]

    @pytest.mark.parametrize(
        "params",
        [
            {"last_id": "abc"},
            {"page_size": 0},
            {"page_size": -1},
            {"cursor_order": "random"},
            {"page_size": "abc"},
            {"last_id": -5},
            {"last_id": 0},
        ],
    )
    def test_invalid_query_params_raise_validation_error(self, paginator, api_rf, params):
        request = _make_request(api_rf, **params)

        with pytest.raises(ValidationError):
            paginator.paginate_queryset(UserVisit.objects.none(), request)
