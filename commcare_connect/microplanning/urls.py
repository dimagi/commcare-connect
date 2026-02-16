from django.urls import path

from commcare_connect.microplanning.views import microplanning_home

app_name = "microplanning"

urlpatterns = [
    path("<slug:opp_id>/", view=microplanning_home, name="microplanning_home"),
]
