from django.urls import path

from commcare_connect.organization import views

app_name = "organization"

urlpatterns = [
    path("organization/", views.organization_home, name="home"),
    path("organization/invite/<str:token>/", views.accept_invite, name="accept_invite"),
    path("organization/invite/<int:invite_id>/revoke", views.revoke_invite, name="revoke_invite"),
    path("organization/member", views.add_members_form, name="add_members"),
    path("organization/member/remove", views.remove_members, name="remove_members"),
    path("organization/member_table", views.org_member_table, name="org_member_table"),
    path("organization/pending_invites_table", views.org_pending_invites_table, name="pending_invites_table"),
]
