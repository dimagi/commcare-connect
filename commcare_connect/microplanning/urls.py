from django.urls import path

from commcare_connect.microplanning.views import get_area_selection_form, get_wards_features, microplanning_home

app_name = "microplanning"

urlpatterns = [
    path("", view=microplanning_home, name="microplanning_home"),
    path("settings/area-selection", view=get_area_selection_form, name="area_selection_form"),
    path("data/wards", view=get_wards_features, name="get_wards_features"),
]
