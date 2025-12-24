from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.db.models import CharField, Count, F, Max, Prefetch, Q, Sum, Value
from django.db.models.functions import Concat
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.timezone import now
from django.views.decorators.http import require_POST
from django.views.generic import ListView, UpdateView

from commcare_connect.opportunity.models import (
    Opportunity,
    OpportunityAccess,
    PaymentInvoice,
    UserVisit,
    VisitReviewStatus,
    VisitValidationStatus,
)
from commcare_connect.opportunity.views import OpportunityInit, OpportunityInitUpdate
from commcare_connect.organization.decorators import (
    org_admin_required,
    org_program_manager_required,
    org_viewer_required,
)
from commcare_connect.organization.models import Organization
from commcare_connect.program.forms import ManagedOpportunityInitForm, ManagedOpportunityInitUpdateForm, ProgramForm
from commcare_connect.program.models import ManagedOpportunity, Program, ProgramApplication, ProgramApplicationStatus
from commcare_connect.program.tasks import (
    send_opportunity_created_email,
    send_program_invite_applied_email,
    send_program_invite_email,
)

from .utils import is_program_manager


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


class ProgramCreateOrUpdate(ProgramManagerMixin, UpdateView):
    model = Program
    form_class = ProgramForm
    template_name = "program/program_form.html"

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
        return reverse("program:home", kwargs={"org_slug": self.request.org.slug})


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


class ManagedOpportunityViewMixin:
    program = None

    def dispatch(self, request, *args, **kwargs):
        try:
            self.program = Program.objects.get(pk=self.kwargs.get("pk"))
        except Program.DoesNotExist:
            messages.error(request, "Program not found.")
            return redirect(reverse("program:home", kwargs={"org_slug": request.org.slug}))
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        is_create = self.object is None
        response = super().form_valid(form)
        if is_create:
            send_opportunity_created_email(self.object.id)
        return response

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["program"] = self.program
        return kwargs


class ManagedOpportunityInit(ManagedOpportunityViewMixin, ProgramManagerMixin, OpportunityInit):
    form_class = ManagedOpportunityInitForm


class ManagedOpportunityInitUpdate(ManagedOpportunityViewMixin, ProgramManagerMixin, OpportunityInitUpdate):
    form_class = ManagedOpportunityInitUpdateForm


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

    send_program_invite_email(obj.id)

    return redirect(reverse("program:home", kwargs={"org_slug": org_slug}))


@org_program_manager_required
@require_POST
def manage_application(request, org_slug, application_id, action):
    application = get_object_or_404(ProgramApplication, id=application_id)
    redirect_url = reverse("program:home", kwargs={"org_slug": org_slug})

    status_mapping = {
        "accept": ProgramApplicationStatus.ACCEPTED,
        "reject": ProgramApplicationStatus.REJECTED,
    }

    new_status = status_mapping.get(action, None)
    if new_status is None:
        return redirect(redirect_url)

    application.status = new_status
    application.modified_by = request.user.email
    application.save()

    return redirect(redirect_url)


@require_POST
@org_admin_required
def apply_or_decline_application(request, application_id, action, org_slug=None, pk=None):
    application = get_object_or_404(ProgramApplication, id=application_id, status=ProgramApplicationStatus.INVITED)

    redirect_url = reverse("program:home", kwargs={"org_slug": org_slug})

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
        return redirect(redirect_url)

    application.status = action_map[action]["status"]
    application.modified_by = request.user.email
    application.save()

    if action == "apply":
        send_program_invite_applied_email(application.id)

    return redirect(redirect_url)


@org_viewer_required
def program_home(request, org_slug):
    org = Organization.objects.get(slug=org_slug)
    if is_program_manager(request):
        return program_manager_home(request, org)
    return network_manager_home(request, org)


def program_manager_home(request, org):
    programs = (
        Program.objects.filter(organization=org)
        .order_by("-start_date")
        .annotate(
            invited=Count("programapplication"),
            applied=Count(
                "programapplication",
                filter=Q(
                    programapplication__status__in=[
                        ProgramApplicationStatus.APPLIED,
                        ProgramApplicationStatus.ACCEPTED,
                    ]
                ),
            ),
            accepted=Count(
                "programapplication",
                filter=Q(programapplication__status=ProgramApplicationStatus.ACCEPTED),
            ),
        )
    )

    pending_review_data = (
        UserVisit.objects.filter(
            status=VisitValidationStatus.approved,
            review_status=VisitReviewStatus.pending,
            opportunity__managed=True,
            opportunity__managedopportunity__program__in=programs,
        )
        .values("opportunity__id", "opportunity__name", "opportunity__organization__name")
        .annotate(count=Count("id"))
    )

    pending_review = _make_recent_activity_data(pending_review_data, org.slug, "opportunity:worker_deliver")

    pending_payments_data = (
        PaymentInvoice.objects.filter(
            opportunity__managed=True,
            opportunity__managedopportunity__program__in=programs,
            payment__isnull=True,
        )
        .values("opportunity__id", "opportunity__name", "opportunity__organization__name")
        .annotate(count=Concat(F("opportunity__currency"), Value(" "), Sum("amount"), output_field=CharField()))
    )

    pending_payments = _make_recent_activity_data(
        pending_payments_data, org.slug, "opportunity:invoice_list", small_text=True, opportunity_slug="opp_id"
    )

    organizations = Organization.objects.exclude(pk=org.pk).order_by("name")
    recent_activities = [
        {"title": "Pending Review", "rows": pending_review},
        {"title": "Pending Invoices", "rows": pending_payments},
    ]

    context = {
        "programs": programs,
        "organizations": organizations,
        "recent_activities": recent_activities,
        "is_program_manager": True,
    }
    return render(request, "program/pm_home.html", context)


def network_manager_home(request, org):
    programs = (
        Program.objects.filter(programapplication__organization=org)
        .annotate(
            status=F("programapplication__status"),
            invite_date=F("programapplication__date_created"),
            application_id=F("programapplication__id"),
        )
        .prefetch_related(
            Prefetch(
                "managedopportunity_set",
                queryset=ManagedOpportunity.objects.filter(organization=org),
                to_attr="managed_opportunities_for_org",
            )
        )
    )

    results = sorted(programs, key=lambda x: (x.invite_date, x.start_date), reverse=True)

    pending_review_data = (
        UserVisit.objects.filter(
            status="pending",
            opportunity__managed=True,
            opportunity__organization=org,
        )
        .values("opportunity__id", "opportunity__name", "opportunity__organization__name")
        .annotate(count=Count("id", distinct=True))
    )
    pending_review = _make_recent_activity_data(pending_review_data, org.slug, "opportunity:worker_deliver")
    access_qs = OpportunityAccess.objects.filter(opportunity__managed=True, opportunity__organization=org)

    pending_payments_data_opps = (
        Opportunity.objects.filter(managed=True, organization=org)
        .annotate(
            pending_payment=Sum("opportunityaccess__payment_accrued") - Sum("opportunityaccess__payment__amount")
        )
        .filter(pending_payment__gte=0)
    )
    pending_payments_data = [
        {
            "opportunity__id": data.id,
            "opportunity__name": data.name,
            "opportunity__organization__name": data.organization.name,
            "count": f"{data.currency_code} {data.pending_payment}",
        }
        for data in pending_payments_data_opps
    ]
    pending_payments = _make_recent_activity_data(
        pending_payments_data, org.slug, "opportunity:worker_payments", small_text=True
    )

    three_days_before = now() - timedelta(days=3)
    inactive_workers_data = (
        access_qs.annotate(
            learn_module_date=Max("completedmodule__date"),
            user_visit_date=Max("uservisit__visit_date"),
        )
        .filter(Q(user_visit_date__lte=three_days_before) | Q(learn_module_date__lte=three_days_before))
        .values("opportunity__id", "opportunity__name", "opportunity__organization__name")
        .annotate(count=Count("id", distinct=True))
    )
    inactive_workers = _make_recent_activity_data(inactive_workers_data, org.slug, "opportunity:worker_list")
    recent_activities = [
        {"title": "Pending Review", "rows": pending_review},
        {"title": "Pending Payments", "rows": pending_payments},
        {"title": "Inactive Connect Workers", "rows": inactive_workers},
    ]
    context = {
        "programs": results,
        "recent_activities": recent_activities,
        "is_program_manager": False,
    }
    return render(request, "program/nm_home.html", context)


def _make_recent_activity_data(
    data: list[dict],
    org_slug: str,
    url_slug: str,
    small_text=False,
    opportunity_slug="opp_id",
):
    return [
        {
            "opportunity__name": row["opportunity__name"],
            "opportunity__organization__name": row["opportunity__organization__name"],
            "count": row.get("count", 0),
            "url": reverse(url_slug, kwargs={"org_slug": org_slug, opportunity_slug: row["opportunity__id"]}),
            "small_text": small_text,
        }
        for row in data
    ]
