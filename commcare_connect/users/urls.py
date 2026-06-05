from django.urls import path

from commcare_connect.users import views
from commcare_connect.users.views import (
    AcceptInviteView,
    CheckInvitedUserView,
    CreateUserLinkView,
    NoMembershipsView,
    ResendInvitesView,
    RetrieveUserOTPView,
    SMSStatusCallbackView,
    UserRedirectView,
    UserToggleView,
    UserUpdateView,
    demo_user_tokens,
    start_learn_app,
)

app_name = "users"
urlpatterns = [
    path("redirect/", view=UserRedirectView.as_view(), name="redirect"),
    path("no_memberships/", view=NoMembershipsView.as_view(), name="no_memberships"),
    path("update/", view=UserUpdateView.as_view(), name="update"),
    path("create_user_link/", view=CreateUserLinkView.as_view(), name="create_user_link"),
    path("start_learn_app/", view=start_learn_app, name="start_learn_app"),
    path("accept_invite/<slug:invite_id>/", view=AcceptInviteView.as_view(), name="accept_invite"),
    path("demo_users/", view=demo_user_tokens, name="demo_users"),
    path("sms_status_callback/", SMSStatusCallbackView.as_view(), name="sms_status_callback"),
    path("api_keys/", views.get_api_keys, name="get_api_keys"),
    path("invited_user/", CheckInvitedUserView.as_view(), name="check_invited_user"),
    path("resend_invites/", ResendInvitesView.as_view(), name="resend_invites"),
    path("connect_user_otp/", RetrieveUserOTPView.as_view(), name="connect_user_otp"),
    path("toggles/", UserToggleView.as_view(), name="user_toggle_view"),
    path("internal_features/", views.internal_features, name="internal_features"),
    path("permission_management/", view=views.permission_management, name="permission_management"),
    path(
        "permission_management_search/", view=views.permission_management_search, name="permission_management_search"
    ),
    path(
        "permission_management_update/", view=views.permission_management_update, name="permission_management_update"
    ),
]
