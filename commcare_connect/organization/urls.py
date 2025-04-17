from django.urls import path

from commcare_connect.organization import views

app_name = "organization"

urlpatterns = [
    path("organization/", views.organization_home, name="home"),
    path("organization/invite/<slug:invite_id>/", views.accept_invite, name="accept_invite"),
    path("organization/member", views.add_members_form, name="add_members"),
    path("organization/add_credential", views.add_credential_view, name="add_credential"),
    path("organization/member_view", views.org_member_view, name="org_member_view"),
    path("organization/member_table", views.org_member_table, name="org_member_table"),
    path("organization/add_credential", views.tw_add_credential_view, name="tw_add_credential"),
    path("organization/tw_member", views.tw_add_members_form, name="tw_add_members"),
]
