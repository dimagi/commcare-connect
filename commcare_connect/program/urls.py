from django.urls import path

from commcare_connect.program.views import (
    ManagedOpportunityApplicationList,
    ManagedOpportunityInit,
    ManagedOpportunityList,
    ProgramCreateOrUpdate,
    ProgramList,
    invite_organization,
    manage_application,
)

app_name = "program"
urlpatterns = [
    path("", view=ProgramList.as_view(), name="list"),
    path("init/", view=ProgramCreateOrUpdate.as_view(), name="init"),
    path("<int:pk>/edit", view=ProgramCreateOrUpdate.as_view(), name="edit"),
    path("<int:pk>/view", view=ManagedOpportunityList.as_view(), name="opportunity_list"),
    path("<int:pk>/opportunity-init", view=ManagedOpportunityInit.as_view(), name="opportunity_init"),
    path("<int:pk>/opportunity/<int:opp_id>/invite", view=invite_organization, name="invite_organization"),
    path(
        "<int:pk>/opportunity/<int:opp_id>/applications",
        view=ManagedOpportunityApplicationList.as_view(),
        name="opportunity_application_list",
    ),
    path("application/<int:application_id>/<str:action>", manage_application, name="manage_application"),
]
