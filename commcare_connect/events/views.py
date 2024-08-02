import django_tables2 as tables
from dal.autocomplete import ModelSelect2
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from django_filters import ChoiceFilter, FilterSet, ModelChoiceFilter
from django_filters.views import FilterView
from rest_framework import serializers, status
from rest_framework.generics import ListCreateAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from commcare_connect.opportunity.forms import DateRanges
from commcare_connect.opportunity.models import Opportunity
from commcare_connect.users.models import User

from .models import Event


class EventSerializer(serializers.ModelSerializer):
    class Meta:
        model = Event
        fields = ["date_created", "event_type", "user", "opportunity"]


@method_decorator(csrf_exempt, name="dispatch")
class EventListCreateView(ListCreateAPIView):
    queryset = Event.objects.all()
    serializer_class = EventSerializer
    permission_classes = [IsAuthenticated]

    def create(self, request, *args, **kwargs):
        if not isinstance(request.data, list):
            return Response({"error": "Expected a list of items"}, status=status.HTTP_400_BAD_REQUEST)

        serializer = self.get_serializer(data=request.data, many=True)
        serializer.is_valid(raise_exception=True)

        event_objects = [Event(**item) for item in serializer.validated_data]
        Event.objects.bulk_create(event_objects)

        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)


class EventTable(tables.Table):
    date_created = tables.Column(verbose_name="Time")

    class Meta:
        model = Event
        template_name = "events/htmx_table.html"
        fields = ("user", "opportunity", "event_type", "date_created")


class EventFilter(FilterSet):
    date_range = ChoiceFilter(choices=DateRanges.choices, method="filter_by_date_range", label="Date Range")
    user = ModelChoiceFilter(
        queryset=User.objects.all(),
        widget=ModelSelect2(
            url="users:search",
            attrs={
                "data-placeholder": "All",
            },
        ),
    )
    event_type = ChoiceFilter(choices=[(_type, _type) for _type in Event.get_all_event_types()])

    class Meta:
        model = Event
        fields = ["opportunity", "user", "event_type"]

    def filter_by_date_range(self, queryset, name, value):
        if not value:
            return queryset

        try:
            date_range = DateRanges(value)
            return queryset.filter(
                date_created__gte=date_range.get_cutoff_date(),
            )
        except ValueError:
            return queryset


class EventListView(tables.SingleTableMixin, FilterView):
    table_class = EventTable
    queryset = Event.objects.all()
    filterset_class = EventFilter
    paginate_by = 20

    def get_template_names(self):
        if self.request.htmx:
            template_name = "events/event_table_partial.html"
        else:
            template_name = "events/event_table_htmx.html"

        return template_name

    def get_queryset(self):
        return Event.objects.filter(opportunity__in=Opportunity.objects.filter(organization=self.request.org))
