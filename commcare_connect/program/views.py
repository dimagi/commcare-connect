from collections import defaultdict
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.db.models import Count, F, OuterRef, Q, Subquery, Sum
from django.http import HttpResponse, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.timezone import now
from django.views.decorators.http import require_POST
from django.views.generic import ListView, UpdateView
from django_tables2 import SingleTableView

from commcare_connect.opportunity.models import (
    CompletedModule,
    OpportunityAccess,
    Payment,
    PaymentInvoice,
    UserVisit,
    VisitReviewStatus,
    VisitValidationStatus,
)
from commcare_connect.opportunity.views import OpportunityInit
from commcare_connect.organization.decorators import (
    org_admin_required,
    org_member_required,
    org_program_manager_required,
)
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

    if request.htmx:
        return HttpResponse(status=200, headers={"HX-Refresh": "true"})

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
        if request.htmx:
            return HttpResponseNotAllowed()
        messages.error(request, "Action not allowed.")
        return redirect(redirect_url)

    application.status = new_status
    application.modified_by = request.user.email
    application.save()

    if request.htmx:
        return HttpResponse(status=200, headers={"HX-Refresh": "true"})

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
        if request.htmx:
            return HttpResponseNotAllowed()
        return redirect(redirect_url)

    application.status = action_map[action]["status"]
    application.modified_by = request.user.email
    application.save()

    if request.htmx:
        return HttpResponse(status=200)

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


@org_member_required
def program_home(request, org_slug):
    org = Organization.objects.get(slug=org_slug)
    is_program_manager = request.org.program_manager and (
        (request.org_membership != None and request.org_membership.is_admin) or request.user.is_superuser  # noqa: E711
    )
    if is_program_manager:
        return program_manager_home(request, org)
    return network_manager_home(request, org)


def program_manager_home(request, org):
    programs = (
        Program.objects.filter(organization=org)
        .order_by("-start_date")
        .values("id", "name", "description", "start_date", "end_date", "budget", "delivery_type__name")
    )
    program_map = defaultdict(lambda: {"accepted": 0, "invited": 0, "applied": 0, "applications": []})
    for program in programs:
        program_map[program["id"]].update(program)

    applications = ProgramApplication.objects.filter(program__in=program_map.keys())
    for application in applications:
        program_id = application.program_id
        program_map[program_id]["applications"].append(application)
        program_map[program_id]["invited"] += 1
        program_map[program_id]["applied"] += application.status in (
            ProgramApplicationStatus.APPLIED,
            ProgramApplicationStatus.ACCEPTED,
        )
        program_map[program_id]["accepted"] += application.status == ProgramApplicationStatus.ACCEPTED

    pending_review = (
        UserVisit.objects.filter(
            status=VisitValidationStatus.approved,
            review_status=VisitReviewStatus.pending,
            opportunity__managed=True,
            opportunity__managedopportunity__program__in=program_map.keys(),
        )
        .values("opportunity__id", "opportunity__name", "opportunity__organization__name")
        .annotate(count=Count("id"))
    )

    paid_invoice_ids = Payment.objects.filter(
        invoice__opportunity__managed=True,
        invoice__opportunity__managedopportunity__program__in=program_map.keys(),
    ).values_list("invoice_id", flat=True)
    pending_payments = (
        PaymentInvoice.objects.filter(
            opportunity__managed=True,
            opportunity__managedopportunity__program__in=program_map.keys(),
        )
        .exclude(id__in=paid_invoice_ids)
        .values("opportunity__id", "opportunity__name", "opportunity__organization__name")
        .annotate(count=Count("id"))
    )

    organizations = Organization.objects.filter(program_manager=False).exclude(pk=org.pk)

    context = {
        "programs": program_map.values(),
        "pending_review": pending_review,
        "pending_payments": pending_payments,
        "organizations": organizations,
    }
    return render(request, "program/tailwind/pm_home.html", context)


def network_manager_home(request, org):
    # Get ProgramApplications with related Program details
    apps = (
        ProgramApplication.objects.filter(organization=org)
        .select_related("program", "program__organization", "program__delivery_type")
        .order_by("-date_created")
    )

    programs = [app.program for app in apps if app.status == ProgramApplicationStatus.ACCEPTED]

    # Sort by ProgramApplication date first, then Program start_date
    results = sorted(apps, key=lambda x: (x.date_created, x.program.start_date), reverse=True)
    pending_review = (
        UserVisit.objects.filter(
            status="pending",
            opportunity__managed=True,
            opportunity__managedopportunity__program__in=programs,
            opportunity__organization=org,
        )
        .values("opportunity__id", "opportunity__name", "opportunity__organization__name")
        .annotate(count=Count("id"))
    )
    access_qs = OpportunityAccess.objects.filter(
        opportunity__managed=True, opportunity__managedopportunity__program__in=programs, opportunity__organization=org
    )
    payment_total = Payment.objects.filter(opportunity_access=OuterRef("pk"))
    pending_payments = (
        access_qs.annotate(
            pending_payment=F("payment_accrued")
            - Subquery(payment_total.values("opportunity_access_id").annotate(total=Sum("amount")).values("total"))
        )
        .filter(pending_payment__gte=0)
        .values("opportunity__id", "opportunity__name", "opportunity__organization__name")
        .annotate(count=Count("id"))
    )
    learn_module_date_query = (
        CompletedModule.objects.filter(opportunity_access=OuterRef("pk")).order_by("-date").values("date")[:1]
    )
    user_visit_date_query = (
        UserVisit.objects.filter(opportunity_access=OuterRef("pk")).order_by("-visit_date").values("visit_date")[:1]
    )
    inactive_workers = (
        access_qs.annotate(
            learn_module_date=Subquery(learn_module_date_query),
            user_visit_date=Subquery(user_visit_date_query),
        )
        .filter(
            Q(user_visit_date__lte=now() - timedelta(days=3)) | Q(learn_module_date__lte=now() - timedelta(days=3))
        )
        .values("opportunity__id", "opportunity__name", "opportunity__organization__name")
        .annotate(count=Count("id"))
    )
    context = {
        "program_apps": results,
        "pending_review": pending_review,
        "pending_payments": pending_payments,
        "inactive_workers": inactive_workers,
    }
    return render(request, "program/tailwind/home.html", context)
