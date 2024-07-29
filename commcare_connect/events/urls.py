from django.urls import path

from .views import EventListView

app_name = "events"

urlpatterns = [
    path("", view=EventListView.as_view(), name="event_htmx"),
]
