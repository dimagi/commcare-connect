"""An applying organization's own applications."""

from django.contrib import messages
from django.db.models import F
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import DetailView
from django_filters.views import FilterView
from django_tables2 import SingleTableMixin
from waffle.mixins import WaffleSwitchMixin

from commcare_connect.flags.switch_names import SOLICITATIONS
from commcare_connect.organization.decorators import org_admin_access_required, org_view_access_required
from commcare_connect.solicitation.filters import MyApplicationFilterSet
from commcare_connect.solicitation.models import SolicitationApplication, SolicitationApplicationAnswer
from commcare_connect.solicitation.tables import MyApplicationTable
from commcare_connect.utils.file import stored_file_response
from commcare_connect.utils.tables import get_validated_page_size


@method_decorator(org_view_access_required, name="dispatch")
class MyApplicationsView(WaffleSwitchMixin, SingleTableMixin, FilterView):
    """Lists the current organization's applications, drafts included, with their status."""

    waffle_switch = SOLICITATIONS
    table_class = MyApplicationTable
    filterset_class = MyApplicationFilterSet
    template_name = "solicitation/my_applications.html"

    def get_queryset(self):
        return (
            SolicitationApplication.objects.filter(organization=self.request.org)
            .select_related("solicitation", "organization")
            # Drafts, which have no submitted date, first; pk breaks ties so rows don't move between pages.
            .order_by(F("submitted_date").desc(nulls_first=True), "-pk")
        )

    def get_paginate_by(self, table_data):
        return get_validated_page_size(self.request)


@method_decorator(org_view_access_required, name="dispatch")
class MyApplicationDetailView(WaffleSwitchMixin, DetailView):
    """One of the organization's applications: its status and answers. Admins can withdraw it from here."""

    waffle_switch = SOLICITATIONS
    template_name = "solicitation/my_application_detail.html"
    context_object_name = "application"
    slug_field = "application_id"
    slug_url_kwarg = "application_id"

    def get_queryset(self):
        return SolicitationApplication.objects.filter(organization=self.request.org).select_related(
            "solicitation__organization", "solicitation__country", "solicitation__delivery_type"
        )

    def get_context_data(self, **kwargs):
        answers = {answer.question_id: answer for answer in self.object.answers.all()}
        questions = self.object.solicitation.questions.order_by("display_order", "pk")
        path = [
            {"title": _("My applications"), "url": reverse("my_applications:list", args=[self.request.org.slug])},
            {"title": self.object.solicitation.title, "url": self.request.path},
        ]
        return super().get_context_data(
            solicitation=self.object.solicitation,
            answered_questions=[(question, answers.get(question.pk)) for question in questions],
            path=path,
            **kwargs,
        )


@method_decorator(org_admin_access_required, name="dispatch")
class MyApplicationWithdrawView(WaffleSwitchMixin, View):
    """Withdraws a sent application that hasn't been decided yet."""

    waffle_switch = SOLICITATIONS
    http_method_names = ["post"]

    def post(self, request, org_slug, application_id):
        application = get_object_or_404(
            SolicitationApplication, organization=request.org, application_id=application_id
        )
        if not application.can_withdraw:
            return HttpResponseBadRequest()
        application.withdraw(user_email=request.user.email)
        messages.success(request, _("Application withdrawn."))
        return redirect(application.applicant_url)


@method_decorator(org_view_access_required, name="dispatch")
class MyApplicationAnswerFileView(WaffleSwitchMixin, View):
    """Downloads a file uploaded as an answer, for members of the organization that applied."""

    waffle_switch = SOLICITATIONS

    def get(self, request, org_slug, application_id, answer_id):
        answer = get_object_or_404(
            SolicitationApplicationAnswer,
            pk=answer_id,
            application__organization=request.org,
            application__application_id=application_id,
        )
        if not answer.file:
            raise Http404()
        return stored_file_response(answer.file, filename=answer.file_name)
