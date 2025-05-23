import datetime
import json
import sys
from collections import Counter, defaultdict
from functools import reduce
from http import HTTPStatus

from celery.result import AsyncResult
from crispy_forms.utils import render_crispy_form
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage, storages
from django.db.models import Count, Max, Q, Sum
from django.db.models.functions import Greatest
from django.forms import modelformset_factory
from django.http import FileResponse, Http404, HttpResponse
from django.middleware.csrf import get_token
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.utils.text import slugify
from django.utils.timezone import now
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from django.views.generic import CreateView, DetailView, TemplateView, UpdateView
from django_tables2 import RequestConfig, SingleTableMixin, SingleTableView
from django_tables2.export import TableExport
from geopy import distance

from commcare_connect.connect_id_client import fetch_users
from commcare_connect.form_receiver.serializers import XFormSerializer
from commcare_connect.opportunity.api.serializers import remove_opportunity_access_cache
from commcare_connect.opportunity.app_xml import AppNoBuildException
from commcare_connect.opportunity.forms import (
    AddBudgetExistingUsersForm,
    AddBudgetNewUsersForm,
    DateRanges,
    DeliverUnitFlagsForm,
    FormJsonValidationRulesForm,
    OpportunityChangeForm,
    OpportunityCreationForm,
    OpportunityFinalizeForm,
    OpportunityInitForm,
    OpportunityUserInviteForm,
    OpportunityVerificationFlagsConfigForm,
    PaymentExportForm,
    PaymentInvoiceForm,
    PaymentUnitForm,
    ReviewVisitExportForm,
    SendMessageMobileUsersForm,
    VisitExportForm,
)
from commcare_connect.opportunity.helpers import (
    get_annotated_opportunity_access,
    get_annotated_opportunity_access_deliver_status,
    get_opportunity_delivery_progress,
    get_opportunity_funnel_progress,
    get_opportunity_list_data,
    get_opportunity_worker_progress,
    get_payment_report_data,
    get_worker_learn_table_data,
    get_worker_table_data,
)
from commcare_connect.opportunity.models import (
    BlobMeta,
    CatchmentArea,
    CompletedModule,
    CompletedWork,
    CompletedWorkStatus,
    DeliverUnit,
    DeliverUnitFlagRules,
    FormJsonValidationRules,
    LearnModule,
    Opportunity,
    OpportunityAccess,
    OpportunityClaim,
    OpportunityClaimLimit,
    OpportunityVerificationFlags,
    Payment,
    PaymentInvoice,
    PaymentUnit,
    UserInvite,
    UserInviteStatus,
    UserVisit,
    VisitReviewStatus,
    VisitValidationStatus,
)
from commcare_connect.opportunity.tables import (
    CompletedWorkTable,
    DeliverStatusTable,
    DeliverUnitTable,
    LearnModuleTable,
    LearnStatusTable,
    OpportunityPaymentTable,
    OpportunityTable,
    PaymentInvoiceTable,
    PaymentReportTable,
    PaymentUnitTable,
    ProgramManagerOpportunityTable,
    SuspendedUsersTable,
    UserStatusTable,
    UserVisitVerificationTable,
    WorkerDeliveryTable,
    WorkerLearnStatusTable,
    WorkerLearnTable,
    WorkerPaymentsTable,
    WorkerStatusTable, header_with_tooltip,
)
from commcare_connect.opportunity.tasks import (
    add_connect_users,
    bulk_update_payments_task,
    create_learn_modules_and_deliver_units,
    generate_catchment_area_export,
    generate_deliver_status_export,
    generate_payment_export,
    generate_review_visit_export,
    generate_user_status_export,
    generate_visit_export,
    generate_work_status_export,
    invite_user,
    send_push_notification_task,
    send_sms_task,
    update_user_and_send_invite,
)
from commcare_connect.opportunity.visit_import import (
    ImportException,
    bulk_update_catchments,
    bulk_update_completed_work_status,
    bulk_update_visit_review_status,
    bulk_update_visit_status,
    get_exchange_rate,
    update_payment_accrued,
)
from commcare_connect.organization.decorators import org_admin_required, org_member_required, org_viewer_required
from commcare_connect.program.models import ManagedOpportunity
from commcare_connect.program.utils import is_program_manager, is_program_manager_of_opportunity
from commcare_connect.users.models import User
from commcare_connect.utils.celery import CELERY_TASK_SUCCESS, get_task_progress_message
from commcare_connect.utils.commcarehq_api import get_applications_for_user_by_domain, get_domains_for_user
from commcare_connect.utils.file import get_file_extension
from commcare_connect.utils.tables import get_duration_min
from django.utils.translation import gettext as _

from django.contrib.humanize.templatetags.humanize import intcomma


class OrganizationUserMixin(LoginRequiredMixin, UserPassesTestMixin):
    def test_func(self):
        # request.org_membership is a SimpleLazyObject object so `is not None` is always `True`
        return self.request.org_membership != None or self.request.user.is_superuser  # noqa: E711


class OrganizationUserMemberRoleMixin(LoginRequiredMixin, UserPassesTestMixin):
    def test_func(self):
        return (
            self.request.org_membership != None and not self.request.org_membership.is_viewer  # noqa: E711
        ) or self.request.user.is_superuser


def get_opportunity_or_404(pk, org_slug):
    opp = get_object_or_404(Opportunity, id=pk)

    if (opp.organization and opp.organization.slug == org_slug) or (
        opp.managed and opp.managedopportunity.program.organization.slug == org_slug
    ):
        return opp

    raise Http404("Opportunity not found.")


class OrgContextSingleTableView(SingleTableView):
    def get_table_kwargs(self):
        kwargs = super().get_table_kwargs()
        kwargs["org_slug"] = self.request.org.slug
        return kwargs


class OpportunityList(OrganizationUserMixin, SingleTableMixin, TemplateView):
    template_name = "tailwind/pages/opportunities_list.html"
    paginate_by = 15

    def get_table_class(self):
        if self.request.org.program_manager:
            return ProgramManagerOpportunityTable
        return OpportunityTable

    def get_table_kwargs(self):
        kwargs = super().get_table_kwargs()
        kwargs["org_slug"] = self.request.org.slug
        return kwargs

    def get_table_data(self):
        org = self.request.org
        query_set = get_opportunity_list_data(org, self.request.org.program_manager)
        query_set = query_set.order_by("status", "start_date", "end_date")
        return query_set


class OpportunityCreate(OrganizationUserMemberRoleMixin, CreateView):
    template_name = "opportunity/opportunity_create.html"
    form_class = OpportunityCreationForm

    def get_success_url(self):
        return reverse("opportunity:list", args=(self.request.org.slug,))

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["domains"] = get_domains_for_user(self.request.user)
        kwargs["user"] = self.request.user
        kwargs["org_slug"] = self.request.org.slug
        return kwargs

    def form_valid(self, form: OpportunityCreationForm) -> HttpResponse:
        response = super().form_valid(form)
        create_learn_modules_and_deliver_units.delay(self.object.id)
        return response


class OpportunityInit(OrganizationUserMemberRoleMixin, CreateView):
    template_name = "opportunity/opportunity_init.html"
    form_class = OpportunityInitForm

    def get_success_url(self):
        return reverse("opportunity:add_payment_units", args=(self.request.org.slug, self.object.id))

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["domains"] = get_domains_for_user(self.request.user)
        kwargs["user"] = self.request.user
        kwargs["org_slug"] = self.request.org.slug
        return kwargs

    def form_valid(self, form: OpportunityInitForm):
        response = super().form_valid(form)
        create_learn_modules_and_deliver_units(self.object.id)
        return response


class OpportunityEdit(OrganizationUserMemberRoleMixin, UpdateView):
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

        end_date = form.cleaned_data["end_date"]
        if end_date:
            opportunity.end_date = end_date
        response = super().form_valid(form)
        return response


class OpportunityFinalize(OrganizationUserMemberRoleMixin, UpdateView):
    model = Opportunity
    template_name = "opportunity/opportunity_finalize.html"
    form_class = OpportunityFinalizeForm

    def dispatch(self, request, *args, **kwargs):
        self.object = self.get_object()
        if self.object.paymentunit_set.count() == 0:
            messages.warning(request, "Please configure payment units before setting budget")
            return redirect("opportunity:add_payment_units", org_slug=request.org.slug, pk=self.object.id)
        return super().dispatch(request, *args, **kwargs)

    def get_success_url(self):
        return reverse("opportunity:detail", args=(self.request.org.slug, self.object.id))

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        opportunity = self.object
        payment_units = opportunity.paymentunit_set.all()
        budget_per_user = 0
        payment_units_max_total = 0
        for pu in payment_units:
            budget_per_user += pu.amount * pu.max_total
            payment_units_max_total += pu.max_total
        kwargs["budget_per_user"] = budget_per_user
        kwargs["current_start_date"] = opportunity.start_date
        kwargs["opportunity"] = opportunity
        kwargs["payment_units_max_total"] = payment_units_max_total
        return kwargs

    def form_valid(self, form):
        opportunity = form.instance
        opportunity.modified_by = self.request.user.email
        start_date = form.cleaned_data["start_date"]
        end_date = form.cleaned_data["end_date"]
        if end_date:
            opportunity.end_date = end_date
        if start_date:
            opportunity.start_date = start_date

        if opportunity.managed:
            ManagedOpportunity.objects.filter(id=opportunity.id).update(
                org_pay_per_visit=form.cleaned_data["org_pay_per_visit"]
            )
        response = super().form_valid(form)
        return response


class OpportunityDashboard(OrganizationUserMixin, DetailView):
    model = Opportunity
    template_name = "tailwind/pages/opportunity_dashboard/dashboard.html"

    def get(self, request, *args, **kwargs):
        self.object = self.get_object()
        if not self.object.is_setup_complete:
            messages.warning(request, "Please complete the opportunity setup to view it")
            return redirect("opportunity:add_payment_units", org_slug=request.org.slug, pk=self.object.id)
        context = self.get_context_data(object=self.object, request=request)
        return self.render_to_response(context)

    def get_context_data(self, object, request, **kwargs):
        context = super().get_context_data(**kwargs)

        learn_module_count = LearnModule.objects.filter(app=object.learn_app).count()
        deliver_unit_count = DeliverUnit.objects.filter(app=object.deliver_app).count()
        payment_unit_count = object.paymentunit_set.count()

        def safe_display(value):
            if value is None:
                return "---"
            if isinstance(value, datetime.date):
                return value.strftime("%Y-%m-%d")
            return str(value)

        context["path"] = [
            {"title": "opportunities", "url": reverse("opportunity:list", kwargs={"org_slug": request.org.slug})},
            {"title": object.name, "url": reverse("opportunity:detail", args=(request.org.slug, object.id))},
        ]

        context["resources"] = [
            {"name": "Learn App", "count": learn_module_count, "icon": "fa-book-open-cover"},
            {"name": "Deliver App", "count": deliver_unit_count, "icon": "fa-clipboard-check"},
            {"name": "Payments Units", "count": payment_unit_count, "icon": "fa-hand-holding-dollar"},
        ]

        context["basic_details"] = [
            {
                "name": "Delivery Type",
                "count": safe_display(object.delivery_type and object.delivery_type.name),
                "icon": "fa-file-check",
            },
            {
                "name": "Start Date",
                "count": safe_display(object.start_date),
                "icon": "fa-calendar-range",
            },
            {
                "name": "End Date",
                "count": safe_display(object.end_date),
                "icon": "fa-arrow-right !text-brand-mango",  # color is also changed",
            },
            {
                "name": "Max Workers",
                "count": header_with_tooltip(safe_display(object.number_of_users),
                                             "Maximum allowed workers in the Opportunity"),
                "icon": "fa-users"
            },
            {
                "name": "Max Service Deliveries",
                "count": header_with_tooltip(safe_display(object.allotted_visits),
                                             "Maximum number of payment units that can be delivered. Each payment unit is a service delivery"),
                "icon": "fa-gears",
            },
            {
                "name": "Max Budget",
                "count": header_with_tooltip(f"{object.currency} {intcomma(object.total_budget)}",
                                             "Maximum payments that can be made for workers and organization"),
                "icon": "fa-money-bill",
            },
        ]
        context["is_program_manager"] = is_program_manager_of_opportunity(request, object)
        context["export_form"] = PaymentExportForm()
        context["export_task_id"] = request.GET.get("export_task_id")
        return context


class OpportunityLearnStatusTableView(OrganizationUserMixin, OrgContextSingleTableView):
    model = OpportunityAccess
    paginate_by = 25
    table_class = LearnStatusTable
    template_name = "tables/single_table.html"

    def get_queryset(self):
        opportunity_id = self.kwargs["pk"]
        opportunity = get_opportunity_or_404(org_slug=self.request.org.slug, pk=opportunity_id)
        return OpportunityAccess.objects.filter(opportunity=opportunity).order_by("user__name")


class OpportunityPaymentTableView(OrganizationUserMixin, OrgContextSingleTableView):
    model = OpportunityAccess
    paginate_by = 25
    table_class = OpportunityPaymentTable
    template_name = "tables/single_table.html"

    def get_queryset(self):
        opportunity_id = self.kwargs["pk"]
        org_slug = self.kwargs["org_slug"]
        opportunity = get_opportunity_or_404(org_slug=org_slug, pk=opportunity_id)
        return OpportunityAccess.objects.filter(opportunity=opportunity, payment_accrued__gte=0).order_by(
            "-payment_accrued"
        )


class OpportunityUserLearnProgress(OrganizationUserMixin, DetailView):
    template_name = "opportunity/user_learn_progress.html"

    def get_queryset(self):
        return OpportunityAccess.objects.filter(opportunity_id=self.kwargs.get("opp_id"))


@org_member_required
def export_user_visits(request, org_slug, pk):
    get_opportunity_or_404(org_slug=request.org.slug, pk=pk)
    form = VisitExportForm(data=request.POST)
    if not form.is_valid():
        messages.error(request, form.errors)
        return redirect("opportunity:worker_list", request.org.slug, pk)

    export_format = form.cleaned_data["format"]
    date_range = DateRanges(form.cleaned_data["date_range"])
    status = form.cleaned_data["status"]
    flatten = form.cleaned_data["flatten_form_data"]
    result = generate_visit_export.delay(pk, date_range, status, export_format, flatten)
    redirect_url = reverse("opportunity:worker_list", args=(request.org.slug, pk))
    return redirect(f"{redirect_url}?active_tab=delivery&export_task_id={result.id}")


@org_member_required
def review_visit_export(request, org_slug, pk):
    get_opportunity_or_404(org_slug=request.org.slug, pk=pk)
    form = ReviewVisitExportForm(data=request.POST)
    redirect_url = reverse("opportunity:worker_list", args=(org_slug, pk))
    redirect_url = f"{redirect_url}?active_tab=delivery"
    if not form.is_valid():
        messages.error(request, form.errors)
        return redirect(redirect_url)

    export_format = form.cleaned_data["format"]
    date_range = DateRanges(form.cleaned_data["date_range"])
    status = form.cleaned_data["status"]

    result = generate_review_visit_export.delay(pk, date_range, status, export_format)
    return redirect(f"{redirect_url}&export_task_id={result.id}")


@org_member_required
@require_GET
def export_status(request, org_slug, task_id):
    task = AsyncResult(task_id)
    task_meta = task._get_task_meta()
    status = task_meta.get("status")
    progress = {"complete": status == CELERY_TASK_SUCCESS, "message": get_task_progress_message(task)}
    if status == "FAILURE":
        progress["error"] = task_meta.get("result")
    return render(
        request,
        "tailwind/components/upload_progress_bar.html",
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
    opportunity = get_opportunity_or_404(org_slug=org_slug, pk=opportunity_id)
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
    opportunity = get_opportunity_or_404(org_slug=org_slug, pk=pk)
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
    url = reverse("opportunity:worker_list", args=(org_slug, pk)) + "?active_tab=delivery"
    return redirect(url)


def review_visit_import(request, org_slug=None, pk=None):
    opportunity = get_opportunity_or_404(org_slug=org_slug, pk=pk)
    file = request.FILES.get("visits")
    redirect_url = reverse("opportunity:worker_list", args=(org_slug, pk))
    redirect_url = f"{redirect_url}?active_tab=delivery"
    try:
        status = bulk_update_visit_review_status(opportunity, file)
    except ImportException as e:
        messages.error(request, e.message)
    else:
        message = f"Visit review updated successfully for {len(status)} visits."
        if status.missing_visits:
            message += status.get_missing_message()
        messages.success(request, mark_safe(message))
    return redirect(redirect_url)


@org_member_required
def add_budget_existing_users(request, org_slug=None, pk=None):
    opportunity = get_opportunity_or_404(org_slug=org_slug, pk=pk)
    opportunity_access = OpportunityAccess.objects.filter(opportunity=opportunity)
    opportunity_claims = OpportunityClaim.objects.filter(opportunity_access__in=opportunity_access)

    form = AddBudgetExistingUsersForm(
        opportunity_claims=opportunity_claims,
        opportunity=opportunity,
        data=request.POST or None,
    )
    if form.is_valid():
        form.save()

        additional_visits = form.cleaned_data.get("additional_visits")
        selected_users = form.cleaned_data.get("selected_users")
        end_date = form.cleaned_data.get("end_date")
        message_parts = []

        if additional_visits and selected_users:
            visit_text = f"{additional_visits} visit{'s' if additional_visits != 1 else ''}"
            user_text = f"{len(selected_users)} worker{'s' if len(selected_users) != 1 else ''}"
            message_parts.append(f"Added {visit_text} to {user_text}.")
            if not opportunity.managed:
                message_parts.append(f"Budget increased by {form.budget_increase:.2f}.")

        if end_date:
            message_parts.append(f"Extended opportunity end date to {end_date} for selected workers.")

        messages.success(request, " ".join(message_parts))
        return redirect("opportunity:add_budget_existing_users", org_slug, pk)

    tabs = [
        {
            "key": "existing_workers",
            "label": "Existing Workers",
        },]
    # Nm are not allowed to increase the managed opportunity budget so do not provide that tab.
    if not opportunity.managed or is_program_manager_of_opportunity(request, opportunity):
        tabs.append({
            "key": "new_workers",
            "label": "New Workers",
        })

    path = [
        {"title": "Opportunities", "url": reverse("opportunity:list", args=(request.org.slug,))},
        {"title": opportunity.name, "url": reverse("opportunity:detail", args=(request.org.slug, opportunity.pk))},
        {"title": "Add budget", }
    ]


    return render(
        request,
        "opportunity/add_visits_existing_users.html",
        {
            "form": form,
            "tabs": tabs,
            "path": path,
            "opportunity_claims": opportunity_claims,
            "budget_per_visit": opportunity.budget_per_visit_new,
            "opportunity": opportunity,
        },
    )


@org_member_required
def add_budget_new_users(request, org_slug=None, pk=None):
    opportunity = get_opportunity_or_404(org_slug=org_slug, pk=pk)
    program_manager = is_program_manager(request)

    form = AddBudgetNewUsersForm(
        opportunity=opportunity,
        program_manager=program_manager,
        data=request.POST or None,
    )

    if form.is_valid():
        form.save()
        budget_increase = form.budget_increase
        direction = "added to" if budget_increase >= 0 else "removed from"
        messages.success(
            request,
            f"{opportunity.currency} {abs(form.budget_increase)} was {direction} the opportunity budget."
        )

        redirect_url = reverse("opportunity:add_budget_existing_users", args=[org_slug, pk])
        redirect_url += "?active_tab=new_users"
        response = HttpResponse()
        response["HX-Redirect"] = redirect_url
        return response

    csrf_token = get_token(request)
    form_html = f"""
        <form id="form-content"
              hx-post="{reverse('opportunity:add_budget_new_users', args=[org_slug, pk])}"
              hx-trigger="submit"
              hx-headers='{{"X-CSRFToken": "{csrf_token}"}}'>
            <input type="hidden" name="csrfmiddlewaretoken" value="{csrf_token}">
            {render_crispy_form(form)}
        </form>
        """

    return HttpResponse(mark_safe(form_html))


class OpportunityUserStatusTableView(OrganizationUserMixin, OrgContextSingleTableView):
    model = OpportunityAccess
    paginate_by = 25
    table_class = UserStatusTable
    template_name = "tables/single_table.html"

    def get_queryset(self):
        opportunity_id = self.kwargs["pk"]
        org_slug = self.kwargs["org_slug"]
        opportunity = get_opportunity_or_404(org_slug=org_slug, pk=opportunity_id)
        access_objects = get_annotated_opportunity_access(opportunity)
        return access_objects


@org_member_required
def export_users_for_payment(request, org_slug, pk):
    get_opportunity_or_404(org_slug=request.org.slug, pk=pk)
    form = PaymentExportForm(data=request.POST)
    if not form.is_valid():
        messages.error(request, form.errors)
        return redirect(f"{reverse('opportunity:worker_list', args=[org_slug, pk])}?active_tab=payments")

    export_format = form.cleaned_data["format"]
    result = generate_payment_export.delay(pk, export_format)
    redirect_url = reverse("opportunity:worker_list", args=(request.org.slug, pk))
    return redirect(f"{redirect_url}?export_task_id={result.id}&active_tab=payments")


@org_member_required
@require_POST
def payment_import(request, org_slug=None, pk=None):
    opportunity = get_opportunity_or_404(org_slug=org_slug, pk=pk)
    file = request.FILES.get("payments")
    file_format = get_file_extension(file)
    if file_format not in ("csv", "xlsx"):
        raise ImportException(f"Invalid file format. Only 'CSV' and 'XLSX' are supported. Got {file_format}")

    file_path = f"{opportunity.pk}_{datetime.datetime.now().isoformat}_payment_import"
    saved_path = default_storage.save(file_path, ContentFile(file.read()))
    result = bulk_update_payments_task.delay(opportunity.pk, saved_path, file_format)

    return redirect(
        f"{reverse('opportunity:worker_list', args=[org_slug, pk])}?active_tab=payments&export_task_id={result.id}"
    )


@org_member_required
def add_payment_units(request, org_slug=None, pk=None):
    if request.POST:
        return add_payment_unit(request, org_slug=org_slug, pk=pk)
    opportunity = get_opportunity_or_404(org_slug=org_slug, pk=pk)
    paymentunit_count = PaymentUnit.objects.filter(opportunity=opportunity).count()
    return render(
        request,
        "opportunity/add_payment_units.html",
        dict(opportunity=opportunity, paymentunit_count=paymentunit_count)
    )


@org_member_required
def add_payment_unit(request, org_slug=None, pk=None):
    opportunity = get_opportunity_or_404(org_slug=org_slug, pk=pk)
    deliver_units = DeliverUnit.objects.filter(
        Q(payment_unit__isnull=True) | Q(payment_unit__opportunity__active=False), app=opportunity.deliver_app
    )
    form = PaymentUnitForm(
        deliver_units=deliver_units,
        data=request.POST or None,
        payment_units=opportunity.paymentunit_set.filter(parent_payment_unit__isnull=True).all(),
        org_slug=org_slug,
        opportunity_id=opportunity.pk,
    )
    if form.is_valid():
        form.instance.opportunity = opportunity
        form.save()
        required_deliver_units = form.cleaned_data["required_deliver_units"]
        DeliverUnit.objects.filter(id__in=required_deliver_units, payment_unit__isnull=True).update(
            payment_unit=form.instance.id
        )
        optional_deliver_units = form.cleaned_data["optional_deliver_units"]
        DeliverUnit.objects.filter(id__in=optional_deliver_units, payment_unit__isnull=True).update(
            payment_unit=form.instance.id, optional=True
        )
        sub_payment_units = form.cleaned_data["payment_units"]
        PaymentUnit.objects.filter(id__in=sub_payment_units, parent_payment_unit__isnull=True).update(
            parent_payment_unit=form.instance.id
        )
        messages.success(request, f"Payment unit {form.instance.name} created.")
        claims = OpportunityClaim.objects.filter(opportunity_access__opportunity=opportunity)
        for claim in claims:
            OpportunityClaimLimit.create_claim_limits(opportunity, claim)
        return redirect("opportunity:add_payment_units", org_slug=request.org.slug, pk=opportunity.id)
    elif request.POST:
        messages.error(request, "Invalid Data")
        return redirect("opportunity:add_payment_units", org_slug=request.org.slug, pk=opportunity.id)

    path = [
        {"title": "Opportunities", "url": reverse("opportunity:list", args=(request.org.slug,))},
        {"title": opportunity.name, "url": reverse("opportunity:detail", args=(request.org.slug, opportunity.pk))},
        {"title": "Payment unit",}
    ]
    return render(
        request,
        "partial_form.html" if request.GET.get("partial") == "True" else "form.html",
        dict(title=f"{request.org.slug} - {opportunity.name}", form_title="Payment Unit Create", form=form, path=path),
    )


@org_member_required
def edit_payment_unit(request, org_slug=None, opp_id=None, pk=None):
    opportunity = get_opportunity_or_404(pk=opp_id, org_slug=org_slug)
    payment_unit = get_object_or_404(PaymentUnit, id=pk, opportunity=opportunity)
    deliver_units = DeliverUnit.objects.filter(
        Q(payment_unit__isnull=True) | Q(payment_unit=payment_unit) | Q(payment_unit__opportunity__active=False),
        app=opportunity.deliver_app,
    )
    exclude_payment_units = [payment_unit.pk]
    if payment_unit.parent_payment_unit_id:
        exclude_payment_units.append(payment_unit.parent_payment_unit_id)
    payment_unit_deliver_units = {deliver_unit.pk for deliver_unit in payment_unit.deliver_units.all()}
    opportunity_payment_units = (
        opportunity.paymentunit_set.filter(
            Q(parent_payment_unit=payment_unit.pk) | Q(parent_payment_unit__isnull=True)
        )
        .exclude(pk__in=exclude_payment_units)
        .all()
    )
    form = PaymentUnitForm(
        deliver_units=deliver_units,
        instance=payment_unit,
        data=request.POST or None,
        payment_units=opportunity_payment_units,
        org_slug=org_slug,
        opportunity_id=opportunity.pk,
    )
    if form.is_valid():
        form.save()
        required_deliver_units = form.cleaned_data["required_deliver_units"]
        DeliverUnit.objects.filter(id__in=required_deliver_units).update(payment_unit=form.instance.id, optional=False)
        optional_deliver_units = form.cleaned_data["optional_deliver_units"]
        DeliverUnit.objects.filter(id__in=optional_deliver_units).update(payment_unit=form.instance.id, optional=True)
        sub_payment_units = form.cleaned_data["payment_units"]
        PaymentUnit.objects.filter(id__in=sub_payment_units, parent_payment_unit__isnull=True).update(
            parent_payment_unit=form.instance.id
        )
        # Remove deliver units which are not selected anymore
        deliver_units = required_deliver_units + optional_deliver_units
        removed_deliver_units = payment_unit_deliver_units - {int(deliver_unit) for deliver_unit in deliver_units}
        DeliverUnit.objects.filter(id__in=removed_deliver_units).update(payment_unit=None, optional=False)
        removed_payment_units = {payment_unit.id for payment_unit in opportunity_payment_units} - {
            int(payment_unit_id) for payment_unit_id in sub_payment_units
        }
        PaymentUnit.objects.filter(id__in=removed_payment_units, parent_payment_unit=form.instance.id).update(
            parent_payment_unit=None
        )
        messages.success(request, f"Payment unit {form.instance.name} updated. Please reset the budget")
        return redirect("opportunity:finalize", org_slug=request.org.slug, pk=opportunity.id)

    path = [
        {"title": "Opportunities", "url": reverse("opportunity:list", args=(request.org.slug,))},
        {"title": opportunity.name, "url": reverse("opportunity:detail", args=(request.org.slug, opportunity.pk))},
        {"title": "Payment unit", }
    ]
    return render(
        request,
        "form.html",
        dict(title=f"{request.org.slug} - {opportunity.name}", form_title="Payment Unit Edit", form=form, path=path),
    )


@org_member_required
def export_user_status(request, org_slug, pk):
    get_opportunity_or_404(org_slug=request.org.slug, pk=pk)
    form = PaymentExportForm(data=request.POST)
    if not form.is_valid():
        messages.error(request, form.errors)
        return redirect("opportunity:worker_list", request.org.slug, pk)

    export_format = form.cleaned_data["format"]
    result = generate_user_status_export.delay(pk, export_format)
    redirect_url = reverse("opportunity:worker_list", args=(request.org.slug, pk))
    return redirect(f"{redirect_url}?export_task_id={result.id}")


class OpportunityDeliverStatusTable(OrganizationUserMixin, OrgContextSingleTableView):
    model = OpportunityAccess
    paginate_by = 25
    table_class = DeliverStatusTable
    template_name = "tables/single_table.html"

    def get_queryset(self):
        opportunity_id = self.kwargs["pk"]
        org_slug = self.kwargs["org_slug"]
        opportunity = get_opportunity_or_404(pk=opportunity_id, org_slug=org_slug)
        access_objects = get_annotated_opportunity_access_deliver_status(opportunity)
        return access_objects


@org_member_required
def export_deliver_status(request, org_slug, pk):
    get_opportunity_or_404(pk=pk, org_slug=request.org.slug)
    form = PaymentExportForm(data=request.POST)
    if not form.is_valid():
        messages.error(request, form.errors)
        return redirect("opportunity:detail", request.org.slug, pk)

    export_format = form.cleaned_data["format"]
    result = generate_deliver_status_export.delay(pk, export_format)
    redirect_url = reverse("opportunity:detail", args=(request.org.slug, pk))
    return redirect(f"{redirect_url}?export_task_id={result.id}")


@org_member_required
@require_POST
def payment_delete(request, org_slug=None, opp_id=None, access_id=None, pk=None):
    opportunity = get_opportunity_or_404(pk=opp_id, org_slug=org_slug)
    opportunity_access = get_object_or_404(OpportunityAccess, pk=access_id, opportunity=opportunity)
    payment = get_object_or_404(Payment, opportunity_access=opportunity_access, pk=pk)
    payment.delete()
    redirect_url = reverse("opportunity:worker_list", args=(org_slug, opp_id))
    return redirect(f"{redirect_url}?active_tab=payments")


@org_viewer_required
def user_profile(request, org_slug=None, opp_id=None, pk=None):
    access = get_object_or_404(OpportunityAccess, pk=pk, accepted=True)
    user_visits = UserVisit.objects.filter(opportunity_access=access)
    user_catchments = CatchmentArea.objects.filter(opportunity_access=access)
    user_visit_data = []
    for user_visit in user_visits:
        if not user_visit.location:
            continue
        lat, lng, elevation, precision = list(map(float, user_visit.location.split(" ")))
        user_visit_data.append(
            dict(
                entity_name=user_visit.entity_name,
                visit_date=user_visit.visit_date.date(),
                lat=lat,
                lng=lng,
                precision=precision,
            )
        )
    # user for centering the User visits map
    lat_avg = 0.0
    lng_avg = 0.0
    if user_visit_data:
        lat_avg = reduce(lambda x, y: x + float(y["lat"]), user_visit_data, 0.0) / len(user_visit_data)
        lng_avg = reduce(lambda x, y: x + float(y["lng"]), user_visit_data, 0.0) / len(user_visit_data)

    pending_completed_work_count = len(
        [
            cw
            for cw in CompletedWork.objects.filter(opportunity_access=access, status=CompletedWorkStatus.pending)
            if cw.approved_count
        ]
    )
    user_catchment_data = [
        {
            "name": catchment.name,
            "lat": float(catchment.latitude),
            "lng": float(catchment.longitude),
            "radius": catchment.radius,
            "active": catchment.active,
        }
        for catchment in user_catchments
    ]
    pending_payment = max(access.payment_accrued - access.total_paid, 0)
    return render(
        request,
        "opportunity/user_profile.html",
        context=dict(
            access=access,
            user_visits=user_visit_data,
            lat_avg=lat_avg,
            lng_avg=lng_avg,
            MAPBOX_TOKEN=settings.MAPBOX_TOKEN,
            pending_completed_work_count=pending_completed_work_count,
            pending_payment=pending_payment,
            user_catchments=user_catchment_data,
        ),
    )


@org_admin_required
def send_message_mobile_users(request, org_slug=None, pk=None):
    opportunity = get_opportunity_or_404(pk=pk, org_slug=org_slug)
    user_ids = OpportunityAccess.objects.filter(opportunity=opportunity, accepted=True).values_list(
        "user_id", flat=True
    )
    users = User.objects.filter(pk__in=user_ids)
    form = SendMessageMobileUsersForm(users=users, data=request.POST or None)

    if form.is_valid():
        selected_user_ids = form.cleaned_data["selected_users"]
        title = form.cleaned_data["title"]
        body = form.cleaned_data["body"]
        message_type = form.cleaned_data["message_type"]
        if "notification" in message_type:
            send_push_notification_task.delay(selected_user_ids, title, body)
        if "sms" in message_type:
            send_sms_task.delay(selected_user_ids, body)
        return redirect("opportunity:detail", org_slug=request.org.slug, pk=pk)

    path = [
        {"title": "Opportunities", "url": reverse("opportunity:list", args=(org_slug,))},
        {"title": opportunity.name, "url": reverse("opportunity:detail", args=(org_slug, opportunity.id))},
        {"title": "Send Message", "url": request.path},
    ]
    return render(
        request,
        "opportunity/send_message.html",
        context=dict(
            title=f"{request.org.slug} - {opportunity.name}",
            form_title="Send Message",
            form=form,
            users=users,
            user_ids=list(user_ids),
            path=path,
        ),
    )


# used for loading learn_app and deliver_app dropdowns
@org_member_required
def get_application(request, org_slug=None):
    domain = request.GET.get("learn_app_domain") or request.GET.get("deliver_app_domain")
    applications = get_applications_for_user_by_domain(request.user, domain)
    active_opps = Opportunity.objects.filter(
        Q(learn_app__cc_domain=domain) | Q(deliver_app__cc_domain=domain),
        active=True,
        end_date__lt=datetime.date.today(),
    ).select_related("learn_app", "deliver_app")
    existing_apps = set()
    for opp in active_opps:
        if opp.learn_app.cc_domain == domain:
            existing_apps.add(opp.learn_app.cc_app_id)
        if opp.deliver_app.cc_domain == domain:
            existing_apps.add(opp.deliver_app.cc_app_id)
    options = []
    for app in applications:
        if app["id"] not in existing_apps:
            value = json.dumps(app)
            name = app["name"]
            options.append(format_html("<option value='{}'>{}</option>", value, name))
    return HttpResponse("\n".join(options))


@org_member_required
@require_POST
def approve_visit(request, org_slug=None, pk=None):
    user_visit = UserVisit.objects.get(pk=pk)
    if user_visit.status != VisitValidationStatus.approved:
        user_visit.status = VisitValidationStatus.approved
        if user_visit.opportunity.managed:
            user_visit.review_created_on = now()
            if user_visit.review_status == VisitReviewStatus.disagree:
                user_visit.review_status = VisitReviewStatus.pending

            if user_visit.flagged:
                justification = request.POST.get("justification")
                if not justification:
                    messages.error(request, "Justification is mandatory for flagged visits.")
                user_visit.justification = justification

        user_visit.save()
        update_payment_accrued(opportunity=user_visit.opportunity, users=[user_visit.user])

    return HttpResponse(status=200, headers={"HX-Trigger": "reload_table"})


@org_member_required
@require_POST
def reject_visit(request, org_slug=None, pk=None):
    user_visit = UserVisit.objects.get(pk=pk)
    reason = request.POST.get("reason")
    user_visit.status = VisitValidationStatus.rejected
    user_visit.reason = reason
    user_visit.save()
    access = OpportunityAccess.objects.get(user_id=user_visit.user_id, opportunity_id=user_visit.opportunity_id)
    update_payment_accrued(opportunity=access.opportunity, users=[access.user])
    return HttpResponse(status=200, headers={"HX-Trigger": "reload_table"})


@org_member_required
def fetch_attachment(self, org_slug, blob_id):
    blob_meta = BlobMeta.objects.get(blob_id=blob_id)
    attachment = storages["default"].open(blob_id)
    return FileResponse(attachment, filename=blob_meta.name, content_type=blob_meta.content_type)


@org_member_required
def verification_flags_config(request, org_slug=None, pk=None):
    opportunity = get_opportunity_or_404(pk=pk, org_slug=org_slug)
    if opportunity.managed and not is_program_manager_of_opportunity(opportunity):
        return redirect("opportunity:detail", org_slug=org_slug, pk=pk)
    verification_flags = OpportunityVerificationFlags.objects.filter(opportunity=opportunity).first()
    form = OpportunityVerificationFlagsConfigForm(instance=verification_flags, data=request.POST or None)
    deliver_unit_count = DeliverUnit.objects.filter(app=opportunity.deliver_app).count()
    DeliverUnitFlagsFormset = modelformset_factory(
        DeliverUnitFlagRules, DeliverUnitFlagsForm, extra=deliver_unit_count, max_num=deliver_unit_count
    )
    deliver_unit_flags = DeliverUnitFlagRules.objects.filter(opportunity=opportunity)
    deliver_unit_formset = DeliverUnitFlagsFormset(
        form_kwargs={"opportunity": opportunity},
        prefix="deliver_unit",
        queryset=deliver_unit_flags,
        data=request.POST or None,
        initial=[
            {"deliver_unit": du}
            for du in opportunity.deliver_app.deliver_units.exclude(
                id__in=deliver_unit_flags.values_list("deliver_unit")
            )
        ],
    )
    FormJsonValidationRulesFormset = modelformset_factory(
        FormJsonValidationRules,
        FormJsonValidationRulesForm,
        extra=1,
    )
    form_json_formset = FormJsonValidationRulesFormset(
        form_kwargs={"opportunity": opportunity},
        prefix="form_json",
        queryset=FormJsonValidationRules.objects.filter(opportunity=opportunity),
        data=request.POST or None,
    )
    if (
        request.method == "POST"
        and form.is_valid()
        and deliver_unit_formset.is_valid()
        and form_json_formset.is_valid()
    ):
        verification_flags = form.save(commit=False)
        verification_flags.opportunity = opportunity
        verification_flags.save()
        for du_form in deliver_unit_formset.forms:
            if du_form.is_valid() and du_form.cleaned_data != {}:
                du_form.instance.opportunity = opportunity
                du_form.save()
        for fj_form in form_json_formset.forms:
            if fj_form.is_valid() and fj_form.cleaned_data != {}:
                fj_form.instance.opportunity = opportunity
                fj_form.save()
        messages.success(request, "Verification flags saved successfully.")

    path = [
        {"title": "Opportunities", "url": reverse("opportunity:list", args=(org_slug,))},
        {"title": opportunity.name, "url": reverse("opportunity:detail", args=(org_slug, opportunity.id))},
        {"title": "Verification Flags Config", "url": request.path},
    ]
    return render(
        request,
        "opportunity/verification_flags_config.html",
        context=dict(
            opportunity=opportunity,
            title=f"{request.org.slug} - {opportunity.name}",
            form=form,
            deliver_unit_formset=deliver_unit_formset,
            form_json_formset=form_json_formset,
            path=path,
        ),
    )


@org_member_required
@csrf_exempt
@require_http_methods(["DELETE"])
def delete_form_json_rule(request, org_slug=None, opp_id=None, pk=None):
    form_json_rule = FormJsonValidationRules.objects.get(opportunity=opp_id, pk=pk)
    form_json_rule.delete()
    return HttpResponse(status=200)


class OpportunityCompletedWorkTable(OrganizationUserMixin, SingleTableView):
    model = CompletedWork
    paginate_by = 25
    table_class = CompletedWorkTable
    template_name = "tables/single_table.html"

    def get_queryset(self):
        opportunity_id = self.kwargs["pk"]
        org_slug = self.kwargs["org_slug"]
        opportunity = get_opportunity_or_404(org_slug=org_slug, pk=opportunity_id)
        access_objects = OpportunityAccess.objects.filter(opportunity=opportunity)
        return list(
            filter(lambda cw: cw.completed, CompletedWork.objects.filter(opportunity_access__in=access_objects))
        )


@org_member_required
def export_completed_work(request, org_slug, pk):
    get_opportunity_or_404(org_slug=request.org.slug, pk=pk)
    form = PaymentExportForm(data=request.POST)
    if not form.is_valid():
        messages.error(request, form.errors)
        return redirect("opportunity:detail", request.org.slug, pk)

    export_format = form.cleaned_data["format"]
    result = generate_work_status_export.delay(pk, export_format)
    redirect_url = reverse("opportunity:detail", args=(request.org.slug, pk))
    return redirect(f"{redirect_url}?export_task_id={result.id}")


@org_member_required
@require_POST
def update_completed_work_status_import(request, org_slug=None, pk=None):
    opportunity = get_opportunity_or_404(org_slug=org_slug, pk=pk)
    file = request.FILES.get("visits")
    try:
        status = bulk_update_completed_work_status(opportunity, file)
    except ImportException as e:
        messages.error(request, e.message)
    else:
        message = f"Payment Verification status updated successfully for {len(status)} completed works."
        if status.missing_completed_works:
            message += status.get_missing_message()
        messages.success(request, mark_safe(message))
    return redirect("opportunity:detail", org_slug, pk)


@org_member_required
@require_POST
def suspend_user(request, org_slug=None, opp_id=None, pk=None):
    access = get_object_or_404(OpportunityAccess, opportunity_id=opp_id, id=pk)
    access.suspended = True
    access.suspension_date = now()
    access.suspension_reason = request.POST.get("reason", "")
    access.save()

    # Clear the cached opportunity access for the suspended user
    remove_opportunity_access_cache(access.user, access.opportunity)

    return redirect("opportunity:user_visits_list", org_slug, opp_id, pk)


@org_member_required
def revoke_user_suspension(request, org_slug=None, opp_id=None, pk=None):
    access = get_object_or_404(OpportunityAccess, opportunity_id=opp_id, id=pk)
    access.suspended = False
    access.save()
    remove_opportunity_access_cache(access.user, access.opportunity)
    next = request.GET.get("next", "/")
    return redirect(next)


@org_member_required
def suspended_users_list(request, org_slug=None, pk=None):
    opportunity = get_opportunity_or_404(org_slug=org_slug, pk=pk)
    access_objects = OpportunityAccess.objects.filter(opportunity=opportunity, suspended=True)
    table = SuspendedUsersTable(access_objects)
    return render(request, "opportunity/suspended_users.html", dict(table=table, opportunity=opportunity))


@org_member_required
def export_catchment_area(request, org_slug, pk):
    get_opportunity_or_404(org_slug=request.org.slug, pk=pk)
    form = PaymentExportForm(data=request.POST)
    if not form.is_valid():
        messages.error(request, form.errors)
        return redirect("opportunity:detail", request.org.slug, pk)

    export_format = form.cleaned_data["format"]
    result = generate_catchment_area_export.delay(pk, export_format)
    redirect_url = reverse("opportunity:detail", args=(request.org.slug, pk))
    return redirect(f"{redirect_url}?export_task_id={result.id}")


@org_member_required
@require_POST
def import_catchment_area(request, org_slug=None, pk=None):
    opportunity = get_opportunity_or_404(org_slug=org_slug, pk=pk)
    file = request.FILES.get("catchments")
    try:
        status = bulk_update_catchments(opportunity, file)
    except ImportException as e:
        messages.error(request, e.message)
    else:
        message = f"{len(status)} catchment areas were updated successfully and {status.new_catchments} were created."
        messages.success(request, mark_safe(message))
    return redirect("opportunity:detail", org_slug, pk)


@org_member_required
def opportunity_user_invite(request, org_slug=None, pk=None):
    opportunity = get_opportunity_or_404(org_slug=request.org.slug, pk=pk)
    form = OpportunityUserInviteForm(data=request.POST or None, opportunity=opportunity)
    if form.is_valid():
        users = form.cleaned_data["users"]
        if users:
            add_connect_users.delay(users, opportunity.id)
        return redirect("opportunity:detail", request.org.slug, pk)
    return render(
        request,
        "form.html",
        dict(title=f"{request.org.slug} - {opportunity.name}", form_title="Invite Workers", form=form),
    )


@org_member_required
def user_visit_review(request, org_slug, opp_id):
    opportunity = get_opportunity_or_404(opp_id, org_slug)
    is_program_manager = is_program_manager_of_opportunity(request, opportunity)
    if request.POST and is_program_manager:
        review_status = request.POST.get("review_status").lower()
        updated_reviews = request.POST.getlist("pk")
        user_visits = UserVisit.objects.filter(pk__in=updated_reviews)
        if review_status in [VisitReviewStatus.agree.value, VisitReviewStatus.disagree.value]:
            user_visits.update(review_status=review_status)
            update_payment_accrued(opportunity=opportunity, users=[visit.user for visit in user_visits])

    return HttpResponse(status=200, headers={"HX-Trigger": "reload_table"})


@org_member_required
def payment_report(request, org_slug, pk):
    opportunity = get_opportunity_or_404(pk, org_slug)
    if not opportunity.managed:
        return redirect("opportunity:detail", org_slug, pk)
    total_paid_users = (
        Payment.objects.filter(opportunity_access__opportunity=opportunity).aggregate(total=Sum("amount"))["total"]
        or 0
    )
    total_paid_nm = (
        Payment.objects.filter(organization=opportunity.organization).aggregate(total=Sum("amount"))["total"] or 0
    )
    data, total_user_payment_accrued, total_nm_payment_accrued = get_payment_report_data(opportunity)
    table = PaymentReportTable(data)
    RequestConfig(request, paginate={"per_page": 15}).configure(table)

    cards = [
        {
            "amount": f"{opportunity.currency} {total_user_payment_accrued}",
            "icon": "fa-user-friends",
            "label": "Worker",
            "subtext": "Total Accrued",
        },
        {
            "amount": f"{opportunity.currency} {total_paid_users}",
            "icon": "fa-user-friends",
            "label": "Worker",
            "subtext": "Total Paid",
        },
        {
            "amount": f"{opportunity.currency} {total_nm_payment_accrued}",
            "icon": "fa-building",
            "label": "Organization",
            "subtext": "Total Accrued",
        },
        {
            "amount": f"{opportunity.currency} {total_paid_nm}",
            "icon": "fa-building",
            "label": "Organization",
            "subtext": "Total Paid",
        },
    ]

    return render(
        request,
        "tailwind/pages/invoice_payment_report.html",
        context=dict(
            table=table,
            opportunity=opportunity,
            cards=cards,
        ),
    )


@org_member_required
def invoice_list(request, org_slug, pk):
    opportunity = get_opportunity_or_404(pk, org_slug)
    if not opportunity.managed:
        return redirect("opportunity:detail", org_slug, pk)

    program_manager = is_program_manager_of_opportunity(request, opportunity)

    filter_kwargs = dict(opportunity=opportunity)

    queryset = PaymentInvoice.objects.filter(**filter_kwargs).order_by("date")
    csrf_token = get_token(request)

    table = PaymentInvoiceTable(
        queryset,
        org_slug=org_slug,
        opp_id=pk,
        exclude=("actions",) if not program_manager else tuple(),
        csrf_token=csrf_token,
    )

    form = PaymentInvoiceForm(opportunity=opportunity)
    RequestConfig(request, paginate={"per_page": 10}).configure(table)
    return render(
        request,
        "tailwind/pages/invoice_list.html",
        {
            "header_title": "Invoices",
            "opportunity": opportunity,
            "table": table,
            "form": form,
            "program_manager": program_manager,
            "path": [
                {"title": "Opportunities", "url": reverse("opportunity:list", args=(org_slug,))},
                {"title": opportunity.name, "url": reverse("opportunity:detail", args=(org_slug, pk))},
                {"title": "Invoices", "url": reverse("opportunity:invoice_list", args=(org_slug, pk))},
            ],
        },
    )


@org_member_required
def invoice_create(request, org_slug=None, pk=None):
    opportunity = get_opportunity_or_404(pk, org_slug)
    if not opportunity.managed or is_program_manager_of_opportunity(request, opportunity):
        return redirect("opportunity:detail", org_slug, pk)
    form = PaymentInvoiceForm(data=request.POST or None, opportunity=opportunity)
    if request.POST and form.is_valid():
        form.save()
        form = PaymentInvoiceForm(opportunity=opportunity)
        redirect_url = reverse("opportunity:invoice_list", args=[org_slug, pk])
        response = HttpResponse(status=200)
        response["HX-Redirect"] = redirect_url
        return response
    return HttpResponse(render_crispy_form(form))


@org_member_required
@require_POST
def invoice_approve(request, org_slug, pk):
    opportunity = get_opportunity_or_404(pk, org_slug)
    if not opportunity.managed or not (request.org_membership and request.org_membership.is_program_manager):
        return redirect("opportunity:detail", org_slug, pk)
    invoice_ids = request.POST.getlist("pk")
    invoices = PaymentInvoice.objects.filter(opportunity=opportunity, pk__in=invoice_ids, payment__isnull=True)
    rate = get_exchange_rate(opportunity.currency)
    for invoice in invoices:
        amount_in_usd = invoice.amount / rate
        payment = Payment(
            amount=invoice.amount,
            organization=opportunity.organization,
            amount_usd=amount_in_usd,
            invoice=invoice,
        )
        payment.save()
    return redirect("opportunity:invoice_list", org_slug, pk)


@org_member_required
@require_POST
@csrf_exempt
def user_invite_delete(request, org_slug, opp_id, pk):
    opportunity = get_opportunity_or_404(opp_id, org_slug)
    invite = get_object_or_404(UserInvite, pk=pk, opportunity=opportunity)
    if invite.status != UserInviteStatus.not_found:
        return HttpResponse(status=403, data="User Invite cannot be deleted.")
    invite.delete()
    return HttpResponse(status=200, headers={"HX-Trigger": "userStatusReload"})


@org_admin_required
@require_POST
def resend_user_invite(request, org_slug, opp_id, pk):
    user_invite = get_object_or_404(UserInvite, id=pk)

    if user_invite.notification_date and (now() - user_invite.notification_date) < datetime.timedelta(days=1):
        return HttpResponse("You can only send one invitation per user every 24 hours. Please try again later.")

    if user_invite.status == UserInviteStatus.not_found:
        found_user_list = fetch_users([user_invite.phone_number])
        if not found_user_list:
            return HttpResponse("The user is not registered on Connect ID yet. Please ask them to sign up first.")

        connect_user = found_user_list[0]
        update_user_and_send_invite(connect_user, opp_id=pk)
    else:
        user = User.objects.get(phone_number=user_invite.phone_number)
        access, _ = OpportunityAccess.objects.get_or_create(user=user, opportunity_id=opp_id)
        invite_user.delay(user.id, access.pk)

    return HttpResponse("The invitation has been successfully resent to the user.")


def sync_deliver_units(request, org_slug, opp_id):
    status = HTTPStatus.OK
    message = "Delivery unit sync completed."
    try:
        create_learn_modules_and_deliver_units(opp_id)
    except AppNoBuildException:
        status = HTTPStatus.BAD_REQUEST
        message = "Failed to retrieve updates. No available build at the moment."

    return HttpResponse(content=message, status=status)


@org_member_required
def user_visit_verification(request, org_slug, opp_id, pk):
    opportunity = get_opportunity_or_404(opp_id, org_slug)
    opportunity_access = get_object_or_404(OpportunityAccess, opportunity=opportunity, pk=pk)
    is_program_manager = is_program_manager_of_opportunity(request, opportunity)

    user_visit_counts = get_user_visit_counts(opportunity_access_id=pk)
    visits = UserVisit.objects.filter(opportunity_access=opportunity_access)
    flagged_info = defaultdict(lambda: {"name": "", "approved": 0, "pending": 0, "rejected": 0})
    for visit in visits:
        for flag in visit.flags:
            if flag == "form_submission_period":
                flag = "Off Hours"
            if flag == "attachment_missing":
                flag = "No Attachment"
            flag = flag.capitalize()
            if visit.status == VisitValidationStatus.approved:
                flagged_info[flag]["approved"] += 1
            if visit.status == VisitValidationStatus.rejected:
                flagged_info[flag]["rejected"] += 1
            if visit.status in (VisitValidationStatus.pending, VisitValidationStatus.duplicate):
                flagged_info[flag]["pending"] += 1
            flagged_info[flag]["name"] = flag
    flagged_info = flagged_info.values()
    last_payment_details = Payment.objects.filter(opportunity_access=opportunity_access).order_by("-date_paid").first()
    pending_payment = max(opportunity_access.payment_accrued - opportunity_access.total_paid, 0)
    pending_completed_work_count = CompletedWork.objects.filter(
        opportunity_access=opportunity_access, status=CompletedWorkStatus.pending, saved_approved_count__gt=0
    ).count()

    path = []
    if opportunity.managed:
        path.append({"title": "Programs", "url": reverse("program:home", args=(org_slug,))})
        path.append(
            {"title": opportunity.managedopportunity.program.name, "url": reverse("program:home", args=(org_slug,))}
        )
    path.extend(
        [
            {"title": "Opportunities", "url": reverse("opportunity:list", args=(org_slug,))},
            {"title": opportunity.name, "url": reverse("opportunity:detail", args=(org_slug, opp_id))},
            {"title": "Workers", "url": reverse("opportunity:worker_list", args=(org_slug, opp_id))},
            {"title": "Worker", "url": request.path},
        ]
    )

    response = render(
        request,
        "opportunity/user_visit_verification.html",
        context={
            "header_title": "Worker",
            "opportunity_access": opportunity_access,
            "counts": user_visit_counts,
            "flagged_info": flagged_info,
            "last_payment_details": last_payment_details,
            "MAPBOX_TOKEN": settings.MAPBOX_TOKEN,
            "opportunity": opportunity_access.opportunity,
            "is_program_manager": is_program_manager,
            "pending_completed_work_count": pending_completed_work_count,
            "pending_payment": pending_payment,
            "path": path,
        },
    )
    return response


def get_user_visit_counts(opportunity_access_id: int, date=None):
    opportunity_access = OpportunityAccess.objects.get(id=opportunity_access_id)
    visit_count_kwargs = {}
    if opportunity_access.opportunity.managed:
        visit_count_kwargs = dict(
            pending_review=Count(
                "id",
                filter=Q(
                    review_status=VisitReviewStatus.pending,
                    review_created_on__isnull=False,
                ),
            ),
            disagree=Count(
                "id",
                filter=Q(
                    review_status=VisitReviewStatus.disagree,
                    review_created_on__isnull=False,
                ),
            ),
            agree=Count(
                "id",
                filter=Q(
                    status=VisitValidationStatus.approved,
                    review_status=VisitReviewStatus.agree,
                    review_created_on__isnull=False,
                ),
            ),
        )

    filter_kwargs = {"opportunity_access": opportunity_access}
    if date:
        filter_kwargs.update({"visit_date__date": date})

    user_visit_counts = UserVisit.objects.filter(**filter_kwargs).aggregate(
        **visit_count_kwargs,
        approved=Count("id", filter=Q(status=VisitValidationStatus.approved)),
        pending=Count("id", filter=Q(status__in=[VisitValidationStatus.pending, VisitValidationStatus.duplicate])),
        rejected=Count("id", filter=Q(status=VisitValidationStatus.rejected)),
        flagged=Count("id", filter=Q(flagged=True)),
        total=Count("*"),
    )
    return user_visit_counts


class VisitVerificationTableView(OrganizationUserMixin, SingleTableView):
    model = UserVisit
    table_class = UserVisitVerificationTable
    template_name = "opportunity/user_visit_verification_table.html"
    exclude_columns = []

    def get_paginate_by(self, table_data):
        return self.request.GET.get("per_page", 10)

    def get_table(self, **kwargs):
        kwargs["exclude"] = self.exclude_columns
        self.table = super().get_table(**kwargs)
        return self.table

    def dispatch(self, request, *args, **kwargs):
        response = super().dispatch(request, *args, **kwargs)
        url = reverse(
            "opportunity:user_visits_list",
            args=[request.org.slug, self.kwargs["opp_id"], self.kwargs["pk"]],
        )
        query_params = request.GET.urlencode()
        response["HX-Replace-Url"] = f"{url}?{query_params}"
        return response

    def get_table_kwargs(self):
        kwargs = super().get_table_kwargs()
        kwargs["organization"] = self.request.org
        return kwargs

    def get_context_data(self, **kwargs):
        user_visit_counts = get_user_visit_counts(self.kwargs["pk"], self.filter_date)

        if self.is_program_manager:
            tabs = [
                {
                    "name": "pending_review",
                    "label": "Pending Review",
                    "count": user_visit_counts.get("pending_review", 0),
                },
                {
                    "name": "disagree",
                    "label": "Disagree",
                    "count": user_visit_counts.get("disagree", 0),
                },
                {
                    "name": "agree",
                    "label": "Agree",
                    "count": user_visit_counts.get("agree", 0),
                },
                {"name": "all", "label": "All", "count": user_visit_counts.get("total", 0)},
            ]
        else:
            tabs = [
                {
                    "name": "pending",
                    "label": "Pending",
                    "count": user_visit_counts.get("pending", 0),
                }
            ]

            if self.opportunity.managed:
                dynamic_tabs = [
                    {
                        "name": "pending_review",
                        "label": "PM Review",
                        "count": user_visit_counts.get("pending_review", 0),
                    },
                    {
                        "name": "disagree",
                        "label": "Revalidate",
                        "count": user_visit_counts.get("disagree", 0),
                    },
                    {
                        "name": "agree",
                        "label": "Approved",
                        "count": user_visit_counts.get("agree", 0),
                    },
                ]
            else:
                dynamic_tabs = [
                    {
                        "name": "approved",
                        "label": "Approved",
                        "count": user_visit_counts.get("approved", 0),
                    },
                ]

            tabs.extend(dynamic_tabs)
            tabs.extend(
                [
                    {
                        "name": "rejected",
                        "label": "Rejected",
                        "count": user_visit_counts.get("rejected", 0),
                    },
                    {"name": "all", "label": "All", "count": user_visit_counts.get("total", 0)},
                ]
            )

        context = super().get_context_data(**kwargs)
        context["opportunity_access"] = self.opportunity_access
        context["tabs"] = tabs
        return context

    def get_queryset(self):
        self.opportunity = get_opportunity_or_404(self.kwargs["opp_id"], self.kwargs["org_slug"])
        self.opportunity_access = get_object_or_404(
            OpportunityAccess, opportunity=self.opportunity, pk=self.kwargs["pk"]
        )
        self.is_program_manager = is_program_manager_of_opportunity(self.request, self.opportunity)

        self.filter_status = self.request.GET.get("filter_status")
        self.filter_date = self.request.GET.get("filter_date")
        filter_kwargs = {"opportunity_access": self.opportunity_access}
        if self.filter_date:
            date = datetime.datetime.strptime(self.filter_date, "%Y-%m-%d")
            filter_kwargs.update({"visit_date__date": date})

        if self.filter_status == "pending":
            filter_kwargs.update({"status__in": [VisitValidationStatus.pending, VisitValidationStatus.duplicate]})
            self.exclude_columns = ["last_activity"]
        if self.filter_status == "approved":
            filter_kwargs.update({"status": VisitValidationStatus.approved})
        if self.filter_status == "rejected":
            filter_kwargs.update({"status": VisitValidationStatus.rejected})

        if self.filter_status == "pending_review":
            filter_kwargs.update(
                {
                    "review_status": VisitReviewStatus.pending,
                    "review_created_on__isnull": False,
                }
            )
        if self.filter_status == "disagree":
            filter_kwargs.update(
                {
                    "review_status": VisitReviewStatus.disagree,
                    "review_created_on__isnull": False,
                }
            )
        if self.filter_status == "agree":
            filter_kwargs.update(
                {
                    "review_status": VisitReviewStatus.agree,
                    "status": VisitValidationStatus.approved,
                    "review_created_on__isnull": False,
                }
            )

        return UserVisit.objects.filter(**filter_kwargs).order_by("visit_date")


@org_member_required
def user_visit_details(request, org_slug, opp_id, pk):
    opportunity = get_opportunity_or_404(opp_id, org_slug)
    user_visit = get_object_or_404(UserVisit, pk=pk, opportunity=opportunity)
    verification_flags_config = opportunity.opportunityverificationflags
    deliver_unit_flags_config = DeliverUnitFlagRules.objects.filter(opportunity=opportunity, deliver_unit=user_visit.deliver_unit)

    serializer = XFormSerializer(data=user_visit.form_json)
    serializer.is_valid()
    xform = serializer.save()

    visit_data = {}
    user_forms = []
    other_forms = []
    lat = None
    lon = None
    precision = None
    visit_data = {
        "entity_name": user_visit.entity_name,
        "user__name": user_visit.user.name,
        "status": user_visit.get_status_display(),
        "visit_date": user_visit.visit_date,
    }
    closest_distance = sys.maxsize
    if user_visit.location:
        locations = UserVisit.objects.filter(opportunity=user_visit.opportunity).exclude(pk=pk).select_related("user")
        lat, lon, _, precision = user_visit.location.split(" ")
        for loc in locations:
            if loc.location is None:
                continue
            other_lat, other_lon, _, other_precision = loc.location.split(" ")
            dist = distance.distance((lat, lon), (other_lat, other_lon))
            closest_distance = int(min(closest_distance, dist.m))
            if dist.m <= 250:
                visit_data = {
                    "entity_name": loc.entity_name,
                    "user__name": loc.user.name,
                    "status": loc.get_status_display(),
                    "visit_date": loc.visit_date,
                    "url": reverse(
                        "opportunity:user_visit_details",
                        kwargs={"org_slug": request.org.slug, "opp_id": loc.opportunity_id, "pk": loc.pk},
                    ),
                }
                if user_visit.user_id == loc.user_id:
                    user_forms.append((visit_data, dist.m, other_lat, other_lon, other_precision))
                else:
                    other_forms.append((visit_data, dist.m, other_lat, other_lon, other_precision))
        user_forms.sort(key=lambda x: x[1])
        other_forms.sort(key=lambda x: x[1])
        visit_data.update({"lat": lat, "lon": lon, "precision": precision})
    return render(
        request,
        "opportunity/user_visit_details.html",
        context=dict(
            user_visit=user_visit,
            xform=xform,
            user_forms=user_forms[:5],
            other_forms=other_forms[:5],
            visit_data=visit_data,
            is_program_manager=is_program_manager_of_opportunity(request, opportunity),
            closest_distance=closest_distance,
            verification_flags_config=verification_flags_config,
            deliver_unit_flags_config=deliver_unit_flags_config,
        ),
    )


def opportunity_worker(request, org_slug=None, opp_id=None):
    opp = get_opportunity_or_404(opp_id, org_slug)
    base_kwargs = {"org_slug": org_slug, "opp_id": opp_id}
    export_form = PaymentExportForm()
    visit_export_form = VisitExportForm()
    review_visit_export_form = ReviewVisitExportForm()

    path = []
    if opp.managed:
        path.append({"title": "Programs", "url": reverse("program:home", args=(org_slug,))})
        path.append({"title": opp.managedopportunity.program.name, "url": reverse("program:home", args=(org_slug,))})
    path.extend(
        [
            {"title": "Opportunities", "url": reverse("opportunity:list", args=(org_slug,))},
            {"title": opp.name, "url": reverse("opportunity:detail", args=(org_slug, opp_id))},
            {"title": "Workers", "url": reverse("opportunity:worker_list", args=(org_slug, opp_id))},
        ]
    )

    raw_qs = request.GET.urlencode()
    query = f"?{raw_qs}" if raw_qs else ""

    workers_count = UserInvite.objects.filter(opportunity_id=opp_id).exclude(status=UserInviteStatus.not_found).count()
    tabs = [
        {
            "key": "workers",
            "label": f"Workers ({workers_count})",
            "url": reverse("opportunity:worker_table", kwargs=base_kwargs) + query,
            "trigger": "loadWorkers",
        },
        {
            "key": "learn",
            "label": "Learn",
            "url": reverse("opportunity:learn_table", kwargs=base_kwargs) + query,
            "trigger": "loadLearn",
        },
        {
            "key": "delivery",
            "label": "Delivery",
            "url": reverse("opportunity:delivery_table", kwargs=base_kwargs) + query,
            "trigger": "loadDelivery",
        },
        {
            "key": "payments",
            "label": "Payments",
            "url": reverse("opportunity:payments_table", kwargs=base_kwargs) + query,
            "trigger": "loadPayments",
        },
    ]

    is_program_manager = opp.managed and is_program_manager_of_opportunity(request, opp)


    import_export_delivery_urls = {
        "export_url_for_pm": reverse(
            "opportunity:review_visit_export",
            args=(request.org.slug, opp_id),
        ),
        "export_url_for_nm": reverse(
            "opportunity:visit_export",
            args=(request.org.slug, opp_id),
        ),
        "import_url": reverse(
            "opportunity:review_visit_import" if is_program_manager else "opportunity:visit_import",
            args=(request.org.slug, opp_id),
        ),
    }

    import_visit_helper_text = _(
        'The file must contain at least the "Visit ID"{extra} and "Status" column. The import is case-insensitive.'
    ).format(extra=_(', "Justification"') if opp.managed else "")

    export_user_visit_title = _("Import PM Review Sheet" if is_program_manager else "Import Verified Visits")

    return render(
        request,
        "tailwind/pages/opportunity_worker.html",
        {
            "opportunity": opp,
            "tabs": tabs,
            "visit_export_form": visit_export_form,
            # This same form is used for multiple types of export
            "export_form": export_form,
            "export_task_id": request.GET.get("export_task_id"),
            "path": path,
            "import_export_delivery_urls": import_export_delivery_urls,
            "import_visit_helper_text": import_visit_helper_text,
            "export_user_visit_title": export_user_visit_title,
            "review_visit_export_form": review_visit_export_form
        },
    )


@org_member_required
def worker_main(request, org_slug=None, opp_id=None):
    opportunity = get_opportunity_or_404(opp_id, org_slug)
    data = get_worker_table_data(opportunity)
    table = WorkerStatusTable(data)
    RequestConfig(request, paginate={"per_page": 10}).configure(table)
    return render(request, "tailwind/components/tables/table.html", {"table": table})


@org_member_required
def worker_learn(request, org_slug=None, opp_id=None):
    opp = get_opportunity_or_404(opp_id, org_slug)
    data = get_worker_learn_table_data(opp)
    table = WorkerLearnTable(data, org_slug=org_slug, opp_id=opp_id)
    RequestConfig(request, paginate={"per_page": 10}).configure(table)
    return render(request, "tailwind/components/tables/table.html", {"table": table})


@org_member_required
def worker_delivery(request, org_slug=None, opp_id=None):
    opportunity = get_opportunity_or_404(opp_id, org_slug)
    data = get_annotated_opportunity_access_deliver_status(opportunity)
    table = WorkerDeliveryTable(data, org_slug=org_slug, opp_id=opp_id)
    RequestConfig(request, paginate={"per_page": 10}).configure(table)
    return render(request, "tailwind/components/tables/table.html", {"table": table})


@org_member_required
def worker_payments(request, org_slug=None, opp_id=None):
    opportunity = get_opportunity_or_404(opp_id, org_slug)

    query_set = OpportunityAccess.objects.filter(opportunity=opportunity, payment_accrued__gte=0,
                                                 accepted=True).order_by(
        "-payment_accrued"
    )
    query_set = query_set.annotate(
        last_active=Greatest(Max("uservisit__visit_date"), Max("completedmodule__date"), "date_learn_started"),
        last_paid=Max("payment__date_paid"),
    )
    table = WorkerPaymentsTable(query_set, org_slug=org_slug, opp_id=opp_id)
    RequestConfig(request, paginate={"per_page": 10}).configure(table)
    return render(request, "tailwind/components/tables/table.html", {"table": table})


@org_member_required
def worker_learn_status_view(request, org_slug, opp_id, access_id):
    access = get_object_or_404(OpportunityAccess, opportunity__id=opp_id, pk=access_id)
    completed_modules = CompletedModule.objects.filter(opportunity_access=access)
    total_duration = datetime.timedelta(0)
    for cm in completed_modules:
        total_duration += cm.duration
    total_duration = get_duration_min(total_duration.total_seconds())

    table = WorkerLearnStatusTable(completed_modules)

    return render(
        request,
        "tailwind/pages/opportunity_worker_learn.html",
        {"header_title": "Worker", "total_learn_duration": total_duration, "table": table, "access": access},
    )


@org_member_required
def worker_payment_history(request, org_slug, opp_id, access_id):
    access = get_object_or_404(OpportunityAccess, opportunity__id=opp_id, pk=access_id)
    queryset = Payment.objects.filter(opportunity_access=access).order_by("-date_paid")
    payments = queryset.values("date_paid", "amount")

    return render(
        request,
        "tailwind/components/worker_page/payment_history.html",
        context=dict(access=access, payments=payments, latest_payment=queryset.first()),
    )


@org_member_required
def worker_flag_counts(request, org_slug, opp_id):
    access_id = request.GET.get("access_id", None)
    filters = {}
    if access_id:
        access = get_object_or_404(OpportunityAccess, opportunity__id=opp_id, pk=access_id)
        filters["completed_work__opportunity_access"] = access
    else:
        opportunity = get_object_or_404(Opportunity, id=opp_id)
        filters["completed_work__opportunity_access__opportunity"] = opportunity

    status = request.GET.get("status", CompletedWorkStatus.pending)
    payment_unit_id = request.GET.get("payment_unit_id")
    filters["completed_work__status"] = status
    if payment_unit_id:
        filters["completed_work__payment_unit__id"] = payment_unit_id

    visits = UserVisit.objects.filter(**filters)
    all_flags = [flag for visit in visits.all() for flag in visit.flags]
    counts = dict(Counter(all_flags))

    completed_work_ids = visits.values_list('completed_work_id', flat=True)
    duplicate_count = CompletedWork.objects.filter(
        id__in=completed_work_ids,
        saved_completed_count__gt=1
    ).count()
    if duplicate_count:
        counts["Duplicate"] = duplicate_count

    return render(
        request,
        "tailwind/components/worker_page/flag_counts.html",
        context=dict(
            flag_counts=counts.items(),
        ),
    )


@org_member_required
def learn_module_table(request, org_slug=None, opp_id=None):
    opp = get_opportunity_or_404(opp_id, org_slug)
    data = LearnModule.objects.filter(app=opp.learn_app)
    table = LearnModuleTable(data)
    return render(request, "tables/single_table.html", {"table": table})


@org_member_required
def deliver_unit_table(request, org_slug=None, opp_id=None):
    opp = get_opportunity_or_404(opp_id, org_slug)
    unit = DeliverUnit.objects.filter(app=opp.deliver_app)
    table = DeliverUnitTable(unit)
    return render(
        request,
        "tables/single_table.html",
        {
            "table": table,
        },
    )


class OpportunityPaymentUnitTableView(OrganizationUserMixin, OrgContextSingleTableView):
    model = PaymentUnit
    table_class = PaymentUnitTable
    template_name = "tables/single_table.html"

    def get_queryset(self):
        opportunity_id = self.kwargs["pk"]
        org_slug = self.kwargs["org_slug"]
        self.opportunity = get_opportunity_or_404(org_slug=org_slug, pk=opportunity_id)
        return PaymentUnit.objects.filter(opportunity=self.opportunity).prefetch_related("deliver_units")

    def get_table_kwargs(self):
        kwargs = super().get_table_kwargs()
        kwargs["org_slug"] = self.request.org.slug
        program_manager = is_program_manager_of_opportunity(self.request, self.opportunity)
        kwargs["can_edit"] = not self.opportunity.managed or program_manager
        if self.opportunity.managed:
            kwargs["org_pay_per_visit"] = self.opportunity.org_pay_per_visit
        return kwargs


@org_member_required
def opportunity_funnel_progress(request, org_slug, opp_id):
    result = get_opportunity_funnel_progress(opp_id)

    accepted = result.workers_invited - result.pending_invites

    funnel_progress = [
        {"stage": "Invited",
         "count": header_with_tooltip(result.workers_invited,
                                      "Number of phone numbers to whom an SMS or push notification was sent and ConnectID exists"),
         "icon": "envelope"},
        {
            "stage": "Accepted",
            "count": header_with_tooltip(accepted,
                                         "Workers that have clicked on the SMS or push notification or gone into Learn app"),
            "icon": "circle-check",
        },
        {"stage": "Started Learning",
         "count": header_with_tooltip(result.started_learning_count,
                                      "Workers that have submitted the first Learn form"),
         "icon": "book-open-cover"
         },
        {"stage": "Completed Learning", "count": header_with_tooltip(result.completed_learning,
                                                                     "Workers that have completed all Learn modules but not assessment"),
         "icon": "book-blank"},
        {"stage": "Completed Assessment",
         "count": header_with_tooltip(result.completed_assessments, "Workers that passed the assessment"),
         "icon": "award-simple"},
        {"stage": "Claimed Job", "count": header_with_tooltip(result.claimed_job,
                                                              "Workers that have read the Opportunity terms and started download of the Deliver app"),
         "icon": "user-check"},
        {"stage": "Started Delivery", "count": header_with_tooltip(result.started_deliveries,
                                                                   "Workers that have submitted at least 1 Learn form"),
         "icon": "house-chimney-user"},
    ]

    return render(
        request,
        "tailwind/pages/opportunity_dashboard/opportunity_funnel_progress.html",
        {"funnel_progress": funnel_progress},
    )


@org_member_required
def opportunity_worker_progress(request, org_slug, opp_id):
    result = get_opportunity_worker_progress(opp_id)

    def safe_percent(numerator, denominator):
        return (numerator / denominator) * 100 if denominator else 0

    verified_percentage = safe_percent(
        result.approved_deliveries or 0,
        result.total_deliveries or 0
    )
    rejected_percentage = safe_percent(
        result.rejected_deliveries or 0,
        result.total_deliveries or 0
    )
    earned_percentage = safe_percent(
        result.total_accrued or 0,
        result.total_budget or 0
    )
    paid_percentage = safe_percent(
        result.total_paid or 0,
        result.total_accrued or 0
    )

    worker_progress = [
        {
            "title": "Verification",
            "progress": [
                {
                    "title": "Approved",
                    "total": header_with_tooltip(result.approved_deliveries,
                                                 "Number of Service Deliveries Approved by both PM and NM or Auto-approved"),
                    "value": header_with_tooltip(f"{verified_percentage:.2f}%",
                                                 "Percentage Approved out of Delivered"),
                    "badge_type": True,
                    "percent": verified_percentage,
                },
                {
                    "title": "Rejected",
                    "total": header_with_tooltip(result.rejected_deliveries,
                                                 "Number of Service Deliveries Rejected"),
                    "value": header_with_tooltip(f"{rejected_percentage:.2f}%",
                                                 "Percentage Rejected out of Delivered"),
                    "badge_type": True,
                    "percent": rejected_percentage,
                },
            ],
        },
        {
            "title": f"Payments to Workers ({result.currency})",
            "progress": [
                {
                    "title": "Earned",
                    "total": header_with_tooltip(result.total_accrued, "Earned Amount"),
                    "value": header_with_tooltip(f"{earned_percentage:.2f}%",
                                                 "Percentage Earned by all workers out of Max Budget in the Opportunity"),
                    "badge_type": True,
                    "percent": earned_percentage,
                },
                {
                    "title": "Paid",
                    "total": header_with_tooltip(result.total_paid, "Paid Amount to All Workers"),
                    "value": header_with_tooltip(f"{paid_percentage:.2f}%",
                                                 "Percentage Paid to all  workers out of Earned amount"),
                    "badge_type": True,
                    "percent": paid_percentage,
                },
            ],
        },
    ]

    return render(
        request,
        "tailwind/pages/opportunity_dashboard/opportunity_worker_progress.html",
        {"worker_progress": worker_progress},
    )



@org_member_required
def opportunity_delivery_stats(request, org_slug, opp_id):
    panel_type_2 = {
        "body": "bg-brand-marigold/10 border border-brand-marigold",
        "icon_bg": "!bg-orange-300",
        "text_color": "!text-orange-500",
    }


    opportunity = get_opportunity_or_404(opp_id, org_slug)

    stats = get_opportunity_delivery_progress(opportunity.id)

    worker_list_url = reverse("opportunity:worker_list", args=(org_slug, opp_id))
    status_url = worker_list_url + "?active_tab=workers&sort=last_active"
    delivery_url = worker_list_url + "?active_tab=delivery&sort=-pending"
    payment_url = worker_list_url + "?active_tab=payments"

    deliveries_panels = [
        {
            "icon": "fa-clipboard-list-check",
            "name": "Services Delivered",
            "status": "Total",
            "value": header_with_tooltip(stats.total_deliveries,
                                         "Total delivered so far excluding duplicates"),
            "url": delivery_url,
            "incr": stats.deliveries_from_yesterday,
        },
        {
            "icon": "fa-clipboard-list-check",
            "name": "Services Delivered",
            "status": "Pending NM Review",
            "value": header_with_tooltip(stats.flagged_deliveries_waiting_for_review,
                                         "Flagged and pending review with NM"),
            "incr": stats.flagged_deliveries_waiting_for_review_since_yesterday,
        },
    ]

    if opportunity.managed:
        deliveries_panels.append(
            {
                "icon": "fa-clipboard-list-check",
                "name": "Services Delivered",
                "status": "Pending PM Review",
                "value": header_with_tooltip(stats.visits_pending_for_pm_review,
                                             "Flagged and pending review with PM"),
                "incr": stats.visits_pending_for_pm_review_since_yesterday,
            }
        )

    opp_stats = [
        {
            "title": "Workers",
            "sub_heading": "",
            "value": "",
            "panels": [
                {"icon": "fa-user-group", "name": "Workers", "status": "Invited", "value": stats.workers_invited,
                 "url": status_url, },
                {
                    "icon": "fa-user-check",
                    "name": "Workers",
                    "status": "Yet to Accept Invitation",
                    "value": stats.pending_invites,
                },
                {
                    "icon": "fa-clipboard-list",
                    "name": "Workers",
                    "status": "Inactive last 3 days",
                    "value": header_with_tooltip(stats.inactive_workers,
                                                 "Did not submit a Learn or Deliver form in the last 3 days"),
                    "url": status_url,
                    **panel_type_2,
                },
            ],
        },
        {
            "title": "Services Delivered",
            "sub_heading": "Last Delivery",
            "value": stats.most_recent_delivery or "--",
            "panels": deliveries_panels,
        },
        {
            "title": f"Worker Payments ({opportunity.currency})",
            "sub_heading": "Last Payment",
            "value": stats.recent_payment or "--",
            "panels": [
                {
                    "icon": "fa-hand-holding-dollar",
                    "name": "Payments",
                    "status": "Earned",
                    "value": header_with_tooltip(intcomma(stats.total_accrued),
                                                 "Worker payment accrued based on approved service deliveries"),
                    "url": payment_url,
                    "incr": stats.accrued_since_yesterday
                },
                {
                    "icon": "fa-hand-holding-droplet",
                    "name": "Payments",
                    "status": "Due",
                    "value": header_with_tooltip(intcomma(stats.payments_due),
                                                 "Worker payments reported as paid"),
                },
            ],
        },
    ]

    return render(
        request, "tailwind/pages/opportunity_dashboard/opportunity_delivery_stat.html", {"opp_stats": opp_stats}
    )
