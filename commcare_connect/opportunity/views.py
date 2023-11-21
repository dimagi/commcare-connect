from http import HTTPStatus

from celery.result import AsyncResult
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.core.files.storage import storages
from django.db.models import F
from django.http import FileResponse, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.safestring import mark_safe
from django.utils.text import slugify
from django.utils.timezone import now
from django.views.decorators.http import require_GET, require_POST
from django.views.generic import CreateView, DetailView, ListView, UpdateView
from django_tables2 import SingleTableView
from django_tables2.export import TableExport

from commcare_connect.opportunity.forms import (
    AddBudgetExistingUsersForm,
    DateRanges,
    OpportunityChangeForm,
    OpportunityCreationForm,
    PaymentExportForm,
    PaymentUnitForm,
    VisitExportForm,
)
from commcare_connect.opportunity.models import (
    CompletedModule,
    DeliverUnit,
    LearnModule,
    Opportunity,
    OpportunityAccess,
    OpportunityClaim,
    Payment,
    PaymentUnit,
    UserVisit,
)
from commcare_connect.opportunity.tables import (
    DeliverUnitTable,
    LearnModuleTable,
    OpportunityAccessTable,
    OpportunityPaymentTable,
    PaymentUnitTable,
    UserPaymentsTable,
    UserStatusTable,
    UserVisitTable,
)
from commcare_connect.opportunity.tasks import (
    add_connect_users,
    create_learn_modules_and_deliver_units,
    generate_payment_export,
    generate_visit_export,
)
from commcare_connect.opportunity.visit_import import (
    ImportException,
    bulk_update_payment_status,
    bulk_update_visit_status,
)
from commcare_connect.organization.decorators import org_admin_required, org_member_required
from commcare_connect.utils.commcarehq_api import get_applications_for_user


class OrganizationUserMixin(LoginRequiredMixin, UserPassesTestMixin):
    def test_func(self):
        return self.request.org_membership is not None


class OpportunityList(OrganizationUserMixin, ListView):
    model = Opportunity
    paginate_by = 10

    def get_queryset(self):
        return Opportunity.objects.filter(organization=self.request.org)


class OpportunityCreate(OrganizationUserMixin, CreateView):
    template_name = "opportunity/opportunity_create.html"
    form_class = OpportunityCreationForm

    def get_success_url(self):
        return reverse("opportunity:list", args=(self.request.org.slug,))

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["applications"] = get_applications_for_user(self.request.user)
        kwargs["user"] = self.request.user
        kwargs["org_slug"] = self.request.org.slug
        return kwargs

    def form_valid(self, form: OpportunityCreationForm) -> HttpResponse:
        response = super().form_valid(form)
        create_learn_modules_and_deliver_units.delay(self.object.id)
        return response


class OpportunityEdit(OrganizationUserMixin, UpdateView):
    model = Opportunity
    template_name = "opportunity/opportunity_edit.html"
    form_class = OpportunityChangeForm

    def get_success_url(self):
        return reverse("opportunity:detail", args=(self.request.org.slug, self.object.id))

    def form_valid(self, form):
        opportunity = form.instance
        opportunity.modified_by = self.request.user.email
        users = form.cleaned_data["users"]
        if users:
            add_connect_users.delay(users, form.instance.id)
        additional_users = form.cleaned_data["additional_users"]
        if additional_users:
            opportunity.total_budget += (
                opportunity.budget_per_visit * opportunity.max_visits_per_user * additional_users
            )
        end_date = form.cleaned_data["end_date"]
        if end_date:
            opportunity.end_date = end_date
        response = super().form_valid(form)
        return response


class OpportunityDetail(OrganizationUserMixin, DetailView):
    model = Opportunity
    template_name = "opportunity/opportunity_detail.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["visit_export_form"] = VisitExportForm()
        context["payment_export_form"] = PaymentExportForm()
        context["export_task_id"] = self.request.GET.get("export_task_id")
        context["refresh_task_id"] = self.request.GET.get("refresh_task_id")
        return context


class OpportunityUserTableView(OrganizationUserMixin, SingleTableView):
    model = OpportunityAccess
    paginate_by = 25
    table_class = OpportunityAccessTable
    template_name = "tables/single_table.html"

    def get_queryset(self):
        opportunity_id = self.kwargs["pk"]
        opportunity = get_object_or_404(Opportunity, organization=self.request.org, id=opportunity_id)
        return OpportunityAccess.objects.filter(opportunity=opportunity).order_by("user__name")


class OpportunityUserVisitTableView(OrganizationUserMixin, SingleTableView):
    model = UserVisit
    paginate_by = 25
    table_class = UserVisitTable
    template_name = "tables/single_table.html"

    def get_queryset(self):
        opportunity_id = self.kwargs["pk"]
        opportunity = get_object_or_404(Opportunity, organization=self.request.org, id=opportunity_id)
        return UserVisit.objects.filter(opportunity=opportunity).order_by("visit_date")


class OpportunityPaymentTableView(OrganizationUserMixin, SingleTableView):
    model = OpportunityAccess
    paginate_by = 25
    table_class = OpportunityPaymentTable
    template_name = "tables/single_table.html"

    def get_queryset(self):
        opportunity_id = self.kwargs["pk"]
        opportunity = get_object_or_404(Opportunity, organization=self.request.org, id=opportunity_id)
        return OpportunityAccess.objects.filter(opportunity=opportunity, payment_accrued__gte=0).order_by(
            "payment_accrued"
        )


class UserPaymentsTableView(OrganizationUserMixin, SingleTableView):
    model = Payment
    paginate_by = 25
    table_class = UserPaymentsTable
    template_name = "opportunity/opportunity_user_payments_list.html"

    def get_queryset(self):
        opportunity_id = self.kwargs["opp_id"]
        opportunity = get_object_or_404(Opportunity, organization=self.request.org, id=opportunity_id)
        access_id = self.kwargs["pk"]
        access = get_object_or_404(OpportunityAccess, opportunity=opportunity, pk=access_id)
        return Payment.objects.filter(opportunity_access=access)


class OpportunityUserLearnProgress(OrganizationUserMixin, DetailView):
    template_name = "opportunity/user_learn_progress.html"

    def get_queryset(self):
        return OpportunityAccess.objects.filter(opportunity_id=self.kwargs.get("opp_id"))

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["completed_modules"] = CompletedModule.objects.filter(
            user=self.object.user,
            opportunity_id=self.kwargs.get("opp_id"),
        )
        return context


@org_member_required
def export_user_visits(request, **kwargs):
    opportunity_id = kwargs["pk"]
    get_object_or_404(Opportunity, organization=request.org, id=opportunity_id)
    form = VisitExportForm(data=request.POST)
    if not form.is_valid():
        messages.error(request, form.errors)
        return redirect("opportunity:detail", request.org.slug, opportunity_id)

    export_format = form.cleaned_data["format"]
    date_range = DateRanges(form.cleaned_data["date_range"])
    status = form.cleaned_data["status"]

    result = generate_visit_export.delay(opportunity_id, date_range, status, export_format)
    redirect_url = reverse("opportunity:detail", args=(request.org.slug, opportunity_id))
    return redirect(f"{redirect_url}?export_task_id={result.id}")


@org_member_required
@require_GET
def export_status(request, org_slug, task_id):
    task_meta = AsyncResult(task_id)._get_task_meta()
    status = task_meta.get("status")
    progress = {
        "complete": status == "SUCCESS",
    }
    if status == "FAILURE":
        progress["error"] = task_meta.get("result")
    return render(
        request,
        "opportunity/upload_progress.html",
        {
            "task_id": task_id,
            "current_time": now().microsecond,
            "progress": progress,
        },
    )


@org_member_required
@require_GET
def download_export(request, org_slug, task_id):
    task_meta = AsyncResult(task_id)._get_task_meta()
    saved_filename = task_meta.get("result")
    opportunity_id = task_meta.get("args")[0]
    opportunity = get_object_or_404(Opportunity, organization=request.org, id=opportunity_id)
    op_slug = slugify(opportunity.name)
    export_format = saved_filename.split(".")[-1]
    filename = f"{org_slug}_{op_slug}_export.{export_format}"

    export_file = storages["default"].open(saved_filename)
    return FileResponse(
        export_file, as_attachment=True, filename=filename, content_type=TableExport.FORMATS[export_format]
    )


@org_member_required
@require_POST
def update_visit_status_import(request, org_slug=None, pk=None):
    opportunity = get_object_or_404(Opportunity, organization=request.org, id=pk)
    file = request.FILES.get("visits")
    try:
        status = bulk_update_visit_status(opportunity, file)
    except ImportException as e:
        messages.error(request, e.message)
    else:
        message = f"Visit status updated successfully for {len(status)} visits."
        if status.missing_visits:
            message += status.get_missing_message()
        messages.success(request, mark_safe(message))
    return redirect("opportunity:detail", org_slug, pk)


@org_member_required
def add_budget_existing_users(request, org_slug=None, pk=None):
    opportunity = get_object_or_404(Opportunity, organization=request.org, id=pk)
    opportunity_access = OpportunityAccess.objects.filter(opportunity=opportunity)
    opportunity_claims = OpportunityClaim.objects.filter(opportunity_access__in=opportunity_access)
    form = AddBudgetExistingUsersForm(opportunity_claims=opportunity_claims)

    if request.method == "POST":
        form = AddBudgetExistingUsersForm(opportunity_claims=opportunity_claims, data=request.POST)
        if form.is_valid():
            selected_users = form.cleaned_data["selected_users"]
            additional_visits = form.cleaned_data["additional_visits"]
            if form.cleaned_data["end_date"]:
                end_date = form.cleaned_data["end_date"]
            OpportunityClaim.objects.filter(pk__in=selected_users).update(
                max_payments=F("max_payments") + additional_visits, end_date=end_date
            )
            return redirect("opportunity:detail", org_slug, pk)

    return render(
        request,
        "opportunity/add_visits_existing_users.html",
        {"form": form, "opportunity_claims": opportunity_claims, "budget_per_visit": opportunity.budget_per_visit},
    )


class OpportunityUserStatusTableView(OrganizationUserMixin, SingleTableView):
    model = OpportunityAccess
    paginate_by = 25
    table_class = UserStatusTable
    template_name = "tables/single_table.html"

    def get_queryset(self):
        opportunity_id = self.kwargs["pk"]
        opportunity = get_object_or_404(Opportunity, organization=self.request.org, id=opportunity_id)
        return OpportunityAccess.objects.filter(opportunity=opportunity).order_by("user__name")


@org_member_required
def export_users_for_payment(request, **kwargs):
    opportunity_id = kwargs["pk"]
    get_object_or_404(Opportunity, organization=request.org, id=opportunity_id)
    form = PaymentExportForm(data=request.POST)
    if not form.is_valid():
        messages.error(request, form.errors)
        return redirect("opportunity:detail", request.org.slug, opportunity_id)

    export_format = form.cleaned_data["format"]
    result = generate_payment_export.delay(opportunity_id, export_format)
    redirect_url = reverse("opportunity:detail", args=(request.org.slug, opportunity_id))
    return redirect(f"{redirect_url}?export_task_id={result.id}")


@org_member_required
@require_POST
def payment_import(request, org_slug=None, pk=None):
    opportunity = get_object_or_404(Opportunity, organization=request.org, id=pk)
    file = request.FILES.get("payments")
    try:
        status = bulk_update_payment_status(opportunity, file)
    except ImportException as e:
        messages.error(request, e.message)
    else:
        message = f"Payment status updated successfully for {len(status)} users."
        messages.success(request, mark_safe(message))
    return redirect("opportunity:detail", org_slug, pk)


@org_member_required
def add_payment_unit(request, org_slug=None, pk=None):
    opportunity = get_object_or_404(Opportunity, organization=request.org, id=pk)
    deliver_units = DeliverUnit.objects.filter(app=opportunity.deliver_app, payment_unit__isnull=True)

    form = PaymentUnitForm(deliver_units=deliver_units)

    if request.method == "POST":
        form = PaymentUnitForm(deliver_units=deliver_units, data=request.POST)
        if form.is_valid():
            form.instance.opportunity = opportunity
            form.save()
            deliver_units = form.cleaned_data["deliver_units"]
            DeliverUnit.objects.filter(id__in=deliver_units, payment_unit__isnull=True).update(
                payment_unit=form.instance.id
            )
            messages.success(request, f"Payment unit {form.instance.name} created.")
            return redirect("opportunity:detail", org_slug=request.org.slug, pk=opportunity.id)

    return render(
        request,
        "form.html",
        dict(title=f"{request.org.slug} - {opportunity.name}", form_title="Payment Unit Create", form=form),
    )


def edit_payment_unit(request, org_slug=None, opp_id=None, pk=None):
    opportunity = get_object_or_404(Opportunity, organization=request.org, id=opp_id)
    payment_unit = get_object_or_404(PaymentUnit, id=pk, opportunity=opportunity)
    deliver_units = DeliverUnit.objects.filter(app=opportunity.deliver_app)
    payment_unit_deliver_units = {deliver_unit.pk for deliver_unit in payment_unit.deliver_units.all()}

    form = PaymentUnitForm(deliver_units=deliver_units, instance=payment_unit)

    if request.method == "POST":
        form = PaymentUnitForm(deliver_units=deliver_units, data=request.POST, instance=payment_unit)
        if form.is_valid():
            form.save()
            deliver_units = form.cleaned_data["deliver_units"]
            DeliverUnit.objects.filter(id__in=deliver_units).update(payment_unit=form.instance.id)
            # Remove deliver units which are not selected anymore
            removed_deliver_units = payment_unit_deliver_units - {int(deliver_unit) for deliver_unit in deliver_units}
            DeliverUnit.objects.filter(id__in=removed_deliver_units).update(payment_unit=None)
            messages.success(request, f"Payment unit {form.instance.name} updated.")
            return redirect("opportunity:detail", org_slug=request.org.slug, pk=opportunity.id)

    return render(
        request,
        "form.html",
        dict(title=f"{request.org.slug} - {opportunity.name}", form_title="Payment Unit Edit", form=form),
    )


class OpportunityPaymentUnitTableView(OrganizationUserMixin, SingleTableView):
    model = PaymentUnit
    paginate_by = 25
    table_class = PaymentUnitTable
    template_name = "tables/single_table.html"

    def get_queryset(self):
        opportunity_id = self.kwargs["pk"]
        opportunity = get_object_or_404(Opportunity, organization=self.request.org, id=opportunity_id)
        return PaymentUnit.objects.filter(opportunity=opportunity)


@org_admin_required
def refresh_app_metadata(request, org_slug=None, pk=None):
    opportunity = get_object_or_404(Opportunity, organization=request.org, id=pk)
    result = create_learn_modules_and_deliver_units.delay(opportunity_id=opportunity.id)
    redirect_url = reverse(
        "opportunity:detail",
        args=(request.org.slug, opportunity.id),
    )
    return redirect(f"{redirect_url}?refresh_task_id={result.id}")


@org_admin_required
@require_GET
def refresh_app_metadata_status(request, org_slug=None, task_id=None):
    task_meta = AsyncResult(task_id)._get_task_meta()
    status = task_meta.get("status")
    if status == "FAILURE":
        HttpResponse(status=HTTPStatus.INTERNAL_SERVER_ERROR, content=task_meta.get("result"))
    if status == "SUCCESS":
        return HttpResponse(status=HTTPStatus.CREATED, headers={"HX-Trigger": "appsRefreshed"})
    return HttpResponse(status=HTTPStatus.ACCEPTED, headers={"HX-Trigger": "appsRefreshing"})


class OpportunityLearnModuleTableView(OrganizationUserMixin, SingleTableView):
    model = LearnModule
    table_class = LearnModuleTable
    template_name = "tables/single_table.html"

    def get_queryset(self):
        opportunity_id = self.kwargs["pk"]
        opportunity = get_object_or_404(Opportunity, organization=self.request.org, id=opportunity_id)
        return LearnModule.objects.filter(app=opportunity.learn_app).order_by("name")


class OpportunityDeliverUnitTableView(OrganizationUserMixin, SingleTableView):
    model = DeliverUnit
    table_class = DeliverUnitTable
    template_name = "tables/single_table.html"

    def get_queryset(self):
        opportunity_id = self.kwargs["pk"]
        opportunity = get_object_or_404(Opportunity, organization=self.request.org, id=opportunity_id)
        return DeliverUnit.objects.filter(app=opportunity.deliver_app).order_by("name")
