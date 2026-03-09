from django.urls import path

from commcare_connect.commcarehq import views

app_name = "commcarehq"
urlpatterns = [
    path("domains/", views.get_domains, name="get_domains"),
    path("applications/", views.get_application, name="get_applications_by_domain"),
]
