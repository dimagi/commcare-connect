"""URLs for demo recordings: the app's own, plus the demo inbox (demo_mail.py)."""

from demo_mail import InboxView
from django.urls import path

from config.urls import urlpatterns as app_urlpatterns

urlpatterns = [
    path("demo-inbox/", InboxView.as_view(), name="demo_inbox"),
    path("demo-inbox/<str:message_id>/", InboxView.as_view(), name="demo_email"),
    *app_urlpatterns,
]
