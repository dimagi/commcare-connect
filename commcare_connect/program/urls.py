from django.urls import path

from commcare_connect.program.views import (
    DeliveryPerformanceTableView,
    FunnelPerformanceTableView,
    ManagedOpportunityInit,
    ManagedOpportunityList,
    ProgramApplicationList,
    ProgramCreateOrUpdate,
    apply_or_decline_application,
    dashboard,
    invite_organization,
    manage_application,
    program_home,
)

app_name = "program"
urlpatterns = [
    path("", view=program_home, name="home"),
    path("init/", view=ProgramCreateOrUpdate.as_view(), name="init"),
    path("<int:pk>/edit", view=ProgramCreateOrUpdate.as_view(), name="edit"),
    path("<int:pk>/view", view=ManagedOpportunityList.as_view(), name="opportunity_list"),
    path("<int:pk>/applications", view=ProgramApplicationList.as_view(), name="applications"),
    path("<int:pk>/opportunity-init", view=ManagedOpportunityInit.as_view(), name="opportunity_init"),
    path("<int:pk>/invite", view=invite_organization, name="invite_organization"),
    path("application/<int:application_id>/<str:action>", manage_application, name="manage_application"),
    path(
        "<int:pk>/application/<int:application_id>/<str:action>/",
        view=apply_or_decline_application,
        name="apply_or_decline_application",
    ),
    path("<int:pk>/dashboard", dashboard, name="dashboard"),
    path("<int:pk>/funnel_performance_table", FunnelPerformanceTableView.as_view(), name="funnel_performance_table"),
    path(
        "<int:pk>/delivery_performance_table",
        DeliveryPerformanceTableView.as_view(),
        name="delivery_performance_table",
    ),
]
