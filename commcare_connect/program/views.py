from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST
from django.views.generic import ListView, UpdateView
from django_tables2 import SingleTableView

from commcare_connect.opportunity.views import OpportunityInit
from commcare_connect.organization.decorators import org_admin_required, org_program_manager_required
from commcare_connect.organization.models import Organization
from commcare_connect.program.forms import ManagedOpportunityInitForm, ProgramForm
from commcare_connect.program.helpers import get_annotated_managed_opportunity, get_delivery_performance_report
from commcare_connect.program.models import ManagedOpportunity, Program, ProgramApplication, ProgramApplicationStatus
from commcare_connect.program.tables import (
    DeliveryPerformanceTable,
    FunnelPerformanceTable,
    ProgramApplicationTable,
    ProgramTable,
)


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
        return Program.objects.filter(organization=self.request.org).order_by("start_date")


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
def invite_organization(request, org_slug, pk):
    requested_org_slug = request.POST.get("organization")
    organization = get_object_or_404(Organization, slug=requested_org_slug)
    if organization == request.org:
        messages.error(request, f"Cannot invite organization {organization.name} to program.")
        return redirect(reverse("program:applications", kwargs={"org_slug": org_slug, "pk": pk}))
    program = get_object_or_404(Program, id=pk)

    obj, created = ProgramApplication.objects.update_or_create(
        program=program,
        organization=organization,
        defaults={
            "status": ProgramApplicationStatus.INVITED,
            "created_by": request.user.email,
            "modified_by": request.user.email,
        },
    )

    if created:
        messages.success(request, "Organization invited successfully!")
    else:
        messages.info(request, "The invitation for this organization has been updated.")

    return redirect(reverse("program:applications", kwargs={"org_slug": org_slug, "pk": pk}))


class ProgramApplicationList(ProgramManagerMixin, SingleTableView):
    model = ProgramApplication
    table_class = ProgramApplicationTable
    paginate_by = 10
    template_name = "program/application_list.html"

    def get_queryset(self):
        program_id = self.kwargs.get("pk")
        return ProgramApplication.objects.filter(program__id=program_id).order_by("date_modified")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["pk"] = self.kwargs.get("pk")
        program = get_object_or_404(Program, id=self.kwargs.get("pk"), organization=self.request.org)

        org_already_member_ids = ProgramApplication.objects.filter(
            program__id=self.kwargs.get("pk"),
            status__in=[ProgramApplicationStatus.ACCEPTED, ProgramApplicationStatus.APPLIED],
        ).values_list("organization_id", flat=True)

        context["organizations"] = Organization.objects.exclude(id__in=[*org_already_member_ids, self.request.org.pk])
        context["program"] = program
        return context


@org_program_manager_required
@require_POST
def manage_application(request, org_slug, application_id, action):
    application = get_object_or_404(ProgramApplication, id=application_id)
    redirect_url = reverse(
        "program:applications",
        kwargs={
            "org_slug": org_slug,
            "pk": application.program.id,
        },
    )

    status_mapping = {
        "accept": ProgramApplicationStatus.ACCEPTED,
        "reject": ProgramApplicationStatus.REJECTED,
    }

    new_status = status_mapping.get(action, None)
    if new_status is None:
        messages.error(request, "Action not allowed.")
        return redirect(redirect_url)

    application.status = new_status
    application.modified_by = request.user.email
    application.save()

    messages.success(request, f"Application has been {action}ed successfully.")
    if application.status == ProgramApplicationStatus.ACCEPTED:
        return redirect("program:opportunity_init", request.org.slug, application.program.id)
    return redirect(redirect_url)


@require_POST
@org_admin_required
def apply_or_decline_application(request, application_id, action, org_slug=None, pk=None):
    application = get_object_or_404(ProgramApplication, id=application_id, status=ProgramApplicationStatus.INVITED)

    redirect_url = reverse("opportunity:list", kwargs={"org_slug": org_slug})

    action_map = {
        "apply": {
            "status": ProgramApplicationStatus.APPLIED,
            "message": f"Application for the program '{application.program.name}' has been "
            f"successfully submitted.",
        },
        "decline": {
            "status": ProgramApplicationStatus.DECLINED,
            "message": f"The application for the program '{application.program.name}' has been marked "
            f"as 'Declined'.",
        },
    }

    if action not in action_map:
        messages.error(request, "Action not allowed.")
        return redirect(redirect_url)

    application.status = action_map[action]["status"]
    application.modified_by = request.user.email
    application.save()

    messages.success(request, action_map[action]["message"])

    return redirect(redirect_url)


@org_program_manager_required
def dashboard(request, **kwargs):
    program = get_object_or_404(Program, id=kwargs.get("pk"), organization=request.org)
    context = {
        "program": program,
    }
    return render(request, "program/dashboard.html", context)


class FunnelPerformanceTableView(ProgramManagerMixin, SingleTableView):
    model = ManagedOpportunity
    paginate_by = 10
    table_class = FunnelPerformanceTable
    template_name = "tables/single_table.html"

    def get_queryset(self):
        program_id = self.kwargs["pk"]
        program = get_object_or_404(Program, id=program_id)
        return get_annotated_managed_opportunity(program)


class DeliveryPerformanceTableView(ProgramManagerMixin, SingleTableView):
    model = ManagedOpportunity
    paginate_by = 10
    table_class = DeliveryPerformanceTable
    template_name = "tables/single_table.html"

    def get_queryset(self):
        program_id = self.kwargs["pk"]
        program = get_object_or_404(Program, id=program_id)
        start_date = self.request.GET.get("start_date") or None
        end_date = self.request.GET.get("end_date") or None
        return get_delivery_performance_report(program, start_date, end_date)
