from django.urls import path

from commcare_connect.program.views import (
    ManagedOpportunityInit,
    ManagedOpportunityInitUpdate,
    ManagedOpportunityList,
    ProgramCreateOrUpdate,
    apply_or_decline_application,
    invite_organization,
    manage_application,
    program_home,
)

app_name = "program"
urlpatterns = [
    path("", view=program_home, name="home"),
    path("init/", view=ProgramCreateOrUpdate.as_view(), name="init"),
    path("<slug:pk>/edit", view=ProgramCreateOrUpdate.as_view(), name="edit"),
    path("<slug:pk>/view", view=ManagedOpportunityList.as_view(), name="opportunity_list"),
    path("<slug:pk>/opportunity-init", view=ManagedOpportunityInit.as_view(), name="opportunity_init"),
    path(
        "<slug:pk>/opportunity/<slug:opp_id>/init/edit/",
        view=ManagedOpportunityInitUpdate.as_view(),
        name="opportunity_init_edit",
    ),
    path("<slug:pk>/invite", view=invite_organization, name="invite_organization"),
    path("application/<slug:application_id>/<str:action>", manage_application, name="manage_application"),
    path(
        "<slug:pk>/application/<slug:application_id>/<str:action>/",
        view=apply_or_decline_application,
        name="apply_or_decline_application",
    ),
]
