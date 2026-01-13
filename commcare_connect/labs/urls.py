from django.urls import path

from . import oauth_views

app_name = "labs"

urlpatterns = [
    path("login/", oauth_views.labs_login_page, name="login"),
    path("initiate/", oauth_views.labs_oauth_login, name="oauth_initiate"),
    path("callback/", oauth_views.labs_oauth_callback, name="oauth_callback"),
    path("logout/", oauth_views.labs_logout, name="logout"),
    path("status/", oauth_views.labs_status, name="status"),
]
