from django.urls import path

from commcare_connect.program.views import ProgramCreateOrUpdate, ProgramList

app_name = "program"
urlpatterns = [
    path("", view=ProgramList.as_view(), name="list"),
    path("init/", view=ProgramCreateOrUpdate.as_view(), name="init"),
    path("<int:pk>/edit", view=ProgramCreateOrUpdate.as_view(), name="edit"),
]
