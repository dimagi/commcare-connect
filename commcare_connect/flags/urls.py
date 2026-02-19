from django.urls import path

from commcare_connect.flags import views

app_name = "flags"

urlpatterns = [
    path("", views.feature_flags, name="feature_flags"),
    path("switch/<str:switch_name>/toggle/", views.toggle_switch, name="toggle_switch"),
    path("flag/<str:flag_name>/update/", views.update_flag, name="update_flag"),
]
