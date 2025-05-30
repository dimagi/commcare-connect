from django.urls import path

from commcare_connect.users.views import (
    SMSStatusCallbackView,
    accept_invite,
    create_user_link_view,
    demo_user_tokens,
    start_learn_app,
    user_detail_view,
    user_redirect_view,
    user_update_view,
)

app_name = "users"
urlpatterns = [
    path("redirect/", view=user_redirect_view, name="redirect"),
    path("update/", view=user_update_view, name="update"),
    path("<int:pk>/", view=user_detail_view, name="detail"),
    path("create_user_link/", view=create_user_link_view, name="create_user_link"),
    path("start_learn_app/", view=start_learn_app, name="start_learn_app"),
    path("accept_invite/<slug:invite_id>/", view=accept_invite, name="accept_invite"),
    path("demo_users/", view=demo_user_tokens, name="demo_users"),
    path("sms_status_callback/", SMSStatusCallbackView.as_view(), name="sms_status_callback"),
]
