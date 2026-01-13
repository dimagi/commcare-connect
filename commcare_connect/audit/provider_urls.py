"""
URL patterns for Connect OAuth provider
"""
from django.urls import path

from . import oauth_views

urlpatterns = [
    path("login/", oauth_views.oauth2_login, name="connect_login"),
    path("login/callback/", oauth_views.oauth2_callback, name="connect_callback"),
]
