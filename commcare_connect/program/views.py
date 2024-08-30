from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.decorators.http import require_POST
from django.views.generic import ListView, UpdateView
from django_tables2 import SingleTableView

from commcare_connect.opportunity.models import Opportunity
from commcare_connect.opportunity.views import OpportunityInit
from commcare_connect.organization.decorators import org_program_manager_required
from commcare_connect.organization.models import Organization
from commcare_connect.program.forms import ManagedOpportunityInitForm, ProgramForm
from commcare_connect.program.models import (
    ManagedOpportunity,
    ManagedOpportunityApplication,
    ManagedOpportunityApplicationStatus,
    Program,
)
from commcare_connect.program.tables import ManagedOpportunityApplicationTable, ProgramTable


class ProgramManagerMixin(LoginRequiredMixin, UserPassesTestMixin):
    def test_func(self):
        org_membership = getattr(self.request, "org_membership", None)
        is_admin = getattr(org_membership, "is_admin", False)
        org = getattr(self.request, "org", None)
        program_manager = getattr(org, "program_manager", False)
        return (org_membership is not None and is_admin and program_manager) or self.request.user.is_superuser


ALLOWED_ORDERINGS = {
    "name": "name",
    "-name": "-name",
    "start_date": "start_date",
    "-start_date": "-start_date",
    "end_date": "end_date",
    "-end_date": "-end_date",
}


class ProgramList(ProgramManagerMixin, SingleTableView):
    model = Program
    paginate_by = 10
    table_class = ProgramTable

    def get_queryset(self):
        return Program.objects.filter(organization=self.request.org)


class ProgramCreateOrUpdate(ProgramManagerMixin, UpdateView):
    model = Program
    form_class = ProgramForm

    def get_object(self, queryset=None):
        pk = self.kwargs.get("pk")
        if pk:
            return super().get_object(queryset)
        return None

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        kwargs["organization"] = self.request.org
        return kwargs

    def form_valid(self, form):
        is_edit = self.object is not None
        response = super().form_valid(form)
        status = ("created", "updated")[is_edit]
        message = f"Program '{self.object.name}' {status} successfully."
        messages.success(self.request, message)
        return response

    def get_success_url(self):
        return reverse("program:list", kwargs={"org_slug": self.request.org.slug})

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["is_edit"] = self.object is not None
        return context

    def get_template_names(self):
        view = ("add", "edit")[self.object is not None]
        template = f"program/program_{view}.html"
        return template


class ManagedOpportunityList(ProgramManagerMixin, ListView):
    model = ManagedOpportunity
    paginate_by = 10
    default_ordering = "name"
    template_name = "opportunity/opportunity_list.html"

    def get_queryset(self):
        ordering = self.request.GET.get("sort", self.default_ordering)
        ordering = ALLOWED_ORDERINGS.get(ordering, self.default_ordering)
        program_id = self.kwargs.get("pk")
        return ManagedOpportunity.objects.filter(program_id=program_id).order_by(ordering)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["program"] = get_object_or_404(Program, id=self.kwargs.get("pk"))
        context["opportunity_init_url"] = reverse(
            "program:opportunity_init", kwargs={"org_slug": self.request.org.slug, "pk": self.kwargs.get("pk")}
        )
        context["base_template"] = "program/base.html"
        return context


class ManagedOpportunityInit(ProgramManagerMixin, OpportunityInit):
    form_class = ManagedOpportunityInitForm
    program = None

    def dispatch(self, request, *args, **kwargs):
        try:
            self.program = Program.objects.get(pk=self.kwargs.get("pk"))
        except Program.DoesNotExist:
            messages.error(request, "Program not found.")
            return redirect(reverse("program:list", kwargs={"org_slug": request.org.slug}))
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["program"] = self.program
        return kwargs


@org_program_manager_required
@require_POST
def invite_organization(request, org_slug, pk, opp_id):
    requested_org_slug = request.POST.get("organization")
    organization = get_object_or_404(Organization, slug=requested_org_slug)
    managed_opp = get_object_or_404(ManagedOpportunity, id=opp_id)

    obj, created = ManagedOpportunityApplication.objects.update_or_create(
        managed_opportunity=managed_opp,
        organization=organization,
        defaults={
            "status": ManagedOpportunityApplicationStatus.INVITED,
            "created_by": request.user.email,
            "modified_by": request.user.email,
        },
    )

    if created:
        messages.success(request, "Organization invited successfully!")
    else:
        messages.info(request, "The invitation for this organization has been updated.")

    return redirect(
        reverse("program:opportunity_application_list", kwargs={"org_slug": org_slug, "pk": pk, "opp_id": opp_id})
    )


class ManagedOpportunityApplicationList(ProgramManagerMixin, SingleTableView):
    model = ManagedOpportunityApplication
    table_class = ManagedOpportunityApplicationTable
    paginate_by = 10
    template_name = "program/managed_opportunity_application_list.html"

    def get_queryset(self):
        opportunity_id = self.kwargs.get("opp_id")
        return ManagedOpportunityApplication.objects.filter(managed_opportunity__id=opportunity_id)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["pk"] = self.kwargs.get("pk")
        opportunity = get_object_or_404(Opportunity, id=self.kwargs.get("opp_id"))

        # Fetch organizations that are not invited, applied, or accepted.
        invited_orgs_ids = ManagedOpportunityApplication.objects.filter(
            managed_opportunity__id=opportunity.id,
            status__in=[
                ManagedOpportunityApplicationStatus.INVITED,
                ManagedOpportunityApplicationStatus.APPLIED,
                ManagedOpportunityApplicationStatus.ACCEPTED,
            ],
        ).values_list("organization_id", flat=True)

        context["organizations"] = Organization.objects.exclude(id__in=invited_orgs_ids)
        context["opportunity"] = opportunity
        return context


@org_program_manager_required
@require_POST
def manage_application(request, org_slug, application_id, action):
    application = get_object_or_404(ManagedOpportunityApplication, id=application_id)
    redirect_url = reverse(
        "program:opportunity_application_list",
        kwargs={
            "org_slug": org_slug,
            "pk": application.managed_opportunity.program.id,
            "opp_id": application.managed_opportunity.id,
        },
    )

    status_mapping = {
        "accept": ManagedOpportunityApplicationStatus.ACCEPTED,
        "reject": ManagedOpportunityApplicationStatus.REJECTED,
    }

    new_status = status_mapping.get(action, None)
    if new_status is None:
        messages.error(request, "Action not allowed.")
        return redirect(redirect_url)

    application.status = new_status
    application.modified_by = request.user.email

    if new_status == ManagedOpportunityApplicationStatus.ACCEPTED:
        application.managed_opportunity.organization = application.organization
        application.managed_opportunity.save()

    application.save()

    messages.success(request, f"Application has been {action}ed successfully.")
    return redirect(redirect_url)
