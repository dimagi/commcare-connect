from django.urls import path

from commcare_connect.program.views import (
    ManagedOpportunityInit,
    ManagedOpportunityList,
    ProgramCreateOrUpdate,
    ProgramList,
)

app_name = "program"
urlpatterns = [
    path("", view=ProgramList.as_view(), name="list"),
    path("init/", view=ProgramCreateOrUpdate.as_view(), name="init"),
    path("<int:pk>/edit", view=ProgramCreateOrUpdate.as_view(), name="edit"),
    path("<int:pk>/view", view=ManagedOpportunityList.as_view(), name="opportunity_list"),
    path("<int:pk>/opportunity-init", view=ManagedOpportunityInit.as_view(), name="opportunity_init"),
]
