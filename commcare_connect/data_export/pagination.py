from rest_framework import serializers
from rest_framework.pagination import BasePagination
from rest_framework.response import Response

FORWARD = "forward"
REVERSE = "reverse"


class _PaginationParamsSerializer(serializers.Serializer):
    last_id = serializers.IntegerField(min_value=1, required=False)
    page_size = serializers.IntegerField(min_value=1, required=False)
    cursor_order = serializers.ChoiceField(choices=[FORWARD, REVERSE], default=FORWARD, required=False)


class IdKeysetPagination(BasePagination):
    """Keyset (cursor) pagination using the integer ``id`` primary key.

    Assumes every model in the queryset has an auto-incrementing integer
    ``id`` column. The cursor is the ``last_id`` value from the previous
    page; the next page returns rows with ``id > last_id`` (forward) or
    ``id < last_id`` (reverse).

    Query parameters:
        last_id      - cursor: id of the last item on the previous page
        page_size    - items per page (default 1000, max 5000)
        cursor_order - ``forward`` (ascending id) or ``reverse`` (descending id)

    All other query parameters are preserved in the ``next`` link.
    """

    default_page_size = 1000
    max_page_size = 5000
    page_size_query_param = "page_size"
    last_id_query_param = "last_id"
    cursor_order_query_param = "cursor_order"

    @property
    def param_field_map(self):
        return {
            "last_id": self.last_id_query_param,
            "page_size": self.page_size_query_param,
            "cursor_order": self.cursor_order_query_param,
        }

    def _get_pagination_params(self, request):
        return {
            field: request.query_params[param]
            for field, param in self.param_field_map.items()
            if param in request.query_params
        }

    def paginate_queryset(self, queryset, request, view=None):
        self.request = request

        params = _PaginationParamsSerializer(data=self._get_pagination_params(request))
        params.is_valid(raise_exception=True)

        self.cursor_order = params.validated_data["cursor_order"]
        self.last_id = params.validated_data.get("last_id")
        raw_page_size = params.validated_data.get("page_size")
        self.page_size = (
            min(raw_page_size, self.max_page_size) if raw_page_size is not None else self.default_page_size
        )

        # Apply ordering and cursor filter
        is_forward = self.cursor_order == FORWARD
        queryset = queryset.order_by("id" if is_forward else "-id")
        if self.last_id is not None:
            queryset = queryset.filter(id__gt=self.last_id) if is_forward else queryset.filter(id__lt=self.last_id)

        # Fetch one extra to detect next page
        results = list(queryset[: self.page_size + 1])
        self.has_next = len(results) > self.page_size
        self.page = results[: self.page_size]

        return self.page

    def get_next_link(self):
        if not self.has_next or not self.page:
            return None

        last_item = self.page[-1]
        query = self.request.query_params.copy()
        query[self.last_id_query_param] = last_item.id
        query[self.page_size_query_param] = self.page_size
        query[self.cursor_order_query_param] = self.cursor_order

        return self.request.build_absolute_uri(f"{self.request.path}?{query.urlencode()}")

    def get_paginated_response(self, data):
        return Response(
            {
                "next": self.get_next_link(),
                "results": data,
            }
        )
