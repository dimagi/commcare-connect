import datetime
import sys
from collections import Counter, defaultdict
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from http import HTTPStatus
from urllib.parse import urlencode, urlparse

from celery.result import AsyncResult
from crispy_forms.utils import render_crispy_form
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.contrib.humanize.templatetags.humanize import intcomma
from django.core.files.storage import default_storage, storages
from django.db.models import Count, DecimalField, FloatField, Func, Max, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Cast, Coalesce
from django.forms import modelformset_factory
from django.http import FileResponse, Http404, HttpResponse, HttpResponseBadRequest
from django.middleware.csrf import get_token
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.utils.text import slugify
from django.utils.timezone import now
from django.utils.translation import gettext as _
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from django.views.generic import CreateView, DetailView, UpdateView
from django_tables2 import RequestConfig, SingleTableView
from django_tables2.export import TableExport
from geopy import distance

from commcare_connect.connect_id_client import fetch_users
from commcare_connect.form_receiver.serializers import XFormSerializer
from commcare_connect.opportunity.api.serializers import remove_opportunity_access_cache
from commcare_connect.opportunity.app_xml import AppNoBuildException
from commcare_connect.opportunity.filters import DeliverFilterSet, FilterMixin, OpportunityListFilterSet
from commcare_connect.opportunity.forms import (
    AddBudgetExistingUsersForm,
    AddBudgetNewUsersForm,
    DateRanges,
    DeliverUnitFlagsForm,
    FormJsonValidationRulesForm,
    HQApiKeyCreateForm,
    OpportunityChangeForm,
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
    OpportunityData,
    get_annotated_opportunity_access_deliver_status,
    get_opportunity_delivery_progress,
    get_opportunity_funnel_progress,
    get_opportunity_worker_progress,
    get_payment_report_data,
    get_worker_learn_table_data,
    get_worker_table_data,
)
from commcare_connect.opportunity.models import (
    BlobMeta,
    CompletedModule,
    CompletedWork,
    CompletedWorkStatus,
    DeliverUnit,
    DeliverUnitFlagRules,
    ExchangeRate,
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
    DeliverUnitTable,
    LearnModuleTable,
    OpportunityTable,
    PaymentInvoiceTable,
    PaymentReportTable,
    PaymentUnitTable,
    ProgramManagerOpportunityTable,
    SuspendedUsersTable,
    UserVisitVerificationTable,
    WorkerDeliveryTable,
    WorkerLearnStatusTable,
    WorkerLearnTable,
    WorkerPaymentsTable,
    WorkerStatusTable,
    header_with_tooltip,
)
from commcare_connect.opportunity.tasks import (
    add_connect_users,
    bulk_update_payments_task,
    bulk_update_visit_status_task,
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
    update_user_and_send_invite,
)
from commcare_connect.opportunity.visit_import import (
    ImportException,
    bulk_update_catchments,
    bulk_update_completed_work_status,
    bulk_update_visit_review_status,
    update_payment_accrued,
)
from commcare_connect.organization.decorators import org_admin_required, org_member_required, org_viewer_required
from commcare_connect.program.models import ManagedOpportunity
from commcare_connect.program.utils import is_program_manager
from commcare_connect.users.models import User
from commcare_connect.utils.celery import CELERY_TASK_SUCCESS, get_task_progress_message
from commcare_connect.utils.file import get_file_extension
from commcare_connect.utils.flags import FlagLabels, Flags
from commcare_connect.utils.permission_const import ALL_ORG_ACCESS
from commcare_connect.utils.tables import get_duration_min, get_validated_page_size


class OrganizationUserMixin(LoginRequiredMixin, UserPassesTestMixin):
    def test_func(self):
        # request.org_membership is a SimpleLazyObject object so `is not None` is always `True`
        return self.request.org_membership != None or self.request.user.has_perm(ALL_ORG_ACCESS)  # noqa: E711


class OrganizationUserMemberRoleMixin(LoginRequiredMixin, UserPassesTestMixin):
    def test_func(self):
        return (
            self.request.org_membership != None and not self.request.org_membership.is_viewer  # noqa: E711
        ) or self.request.user.has_perm(ALL_ORG_ACCESS)


def get_opportunity_or_404(pk, org_slug):
    opp = get_object_or_404(Opportunity, id=pk)

    if (opp.organization and opp.organization.slug == org_slug) or (
        opp.managed and opp.managedopportunity.program.organization.slug == org_slug
    ):
        return opp

    raise Http404("Opportunity not found.")


class OpportunityObjectMixin:
    def get_opportunity(self):
        if not hasattr(self, "_opportunity"):
            opp_id = self.kwargs.get("opp_id")
            org_slug = self.kwargs.get("org_slug")
            self._opportunity = get_opportunity_or_404(opp_id, org_slug)
        return self._opportunity

    def get_object(self, queryset=None):
        return self.get_opportunity()


class OrgContextSingleTableView(SingleTableView):
    def get_table_kwargs(self):
        kwargs = super().get_table_kwargs()
        kwargs["org_slug"] = self.request.org.slug
        return kwargs


class OpportunityList(OrganizationUserMixin, FilterMixin, SingleTableView):
    model = Opportunity
    table_class = ProgramManagerOpportunityTable
    template_name = "opportunity/opportunities_list.html"
    paginate_by = 15
    filter_class = OpportunityListFilterSet

    def get_context_data(self, *args, **kwargs):
        context = super().get_context_data(*args, **kwargs)
        context.update(self.get_filter_context())
        return context

    def get_table_class(self):
        if self.request.org.program_manager:
            return ProgramManagerOpportunityTable
        return OpportunityTable

    def get_paginate_by(self, table):
        return get_validated_page_size(self.request)

    def get_table_kwargs(self):
        kwargs = super().get_table_kwargs()
        kwargs["org_slug"] = self.request.org.slug
        return kwargs

    def get_table_data(self):
        org = self.request.org
        is_program_manager = org.program_manager
        return OpportunityData(org, is_program_manager, self.get_filter_values()).get_data()


class OpportunityInit(OrganizationUserMemberRoleMixin, CreateView):
    template_name = "opportunity/opportunity_init.html"
    form_class = OpportunityInitForm

    def get_success_url(self):
        return reverse("opportunity:add_payment_units", args=(self.request.org.slug, self.object.id))

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        kwargs["org_slug"] = self.request.org.slug
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["api_key_form"] = HQApiKeyCreateForm(auto_id="api_key_form_id_for_%s")
        return context

    def form_valid(self, form: OpportunityInitForm):
        response = super().form_valid(form)
        create_learn_modules_and_deliver_units(self.object.id)
        return response


class OpportunityEdit(OpportunityObjectMixin, OrganizationUserMemberRoleMixin, UpdateView):
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


class OpportunityFinalize(OpportunityObjectMixin, OrganizationUserMemberRoleMixin, UpdateView):
    model = Opportunity
    template_name = "opportunity/opportunity_finalize.html"
    form_class = OpportunityFinalizeForm

    def dispatch(self, request, *args, **kwargs):
        self.object = self.get_object()
        if self.object.paymentunit_set.count() == 0:
            messages.warning(request, "Please configure payment units before setting budget")
            return redirect("opportunity:add_payment_units", org_slug=request.org.slug, opp_id=self.object.id)
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


class OpportunityDashboard(OpportunityObjectMixin, OrganizationUserMixin, DetailView):
    model = Opportunity
    template_name = "opportunity/dashboard.html"

    def get(self, request, *args, **kwargs):
        self.object = self.get_object()
        if not self.object.is_setup_complete:
            messages.warning(request, "Please complete the opportunity setup to view it")
            return redirect("opportunity:add_payment_units", org_slug=request.org.slug, opp_id=self.object.id)
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
            {"title": "Opportunities", "url": reverse("opportunity:list", kwargs={"org_slug": request.org.slug})},
            {"title": object.name, "url": reverse("opportunity:detail", args=(request.org.slug, object.id))},
        ]

        context["resources"] = [
            {"name": "Learn App", "count": learn_module_count, "icon": "fa-book-open"},
            {"name": "Deliver App", "count": deliver_unit_count, "icon": "fa-clipboard-check"},
            {"name": "Payments Units", "count": payment_unit_count, "icon": "fa-hand-holding-dollar"},
        ]

        context["basic_details"] = [
            {
                "name": "Delivery Type",
                "count": safe_display(object.delivery_type and object.delivery_type.name),
                "icon": "fa-file-circle-check",
            },
            {
                "name": "Start Date",
                "count": safe_display(object.start_date),
                "icon": "fa-calendar-days",
            },
            {
                "name": "End Date",
                "count": safe_display(object.end_date),
                "icon": "fa-arrow-right !text-brand-mango",  # color is also changed",
            },
            {
                "name": "Max Connect Workers",
                "count": header_with_tooltip(
                    safe_display(int(object.number_of_users)), "Maximum allowed workers in the Opportunity"
                ),
                "icon": "fa-users",
            },
            {
                "name": "Max Service Deliveries",
                "count": header_with_tooltip(
                    safe_display(int(object.allotted_visits)),
                    "Maximum number of payment units that can be delivered. Each payment unit is a service delivery",
                ),
                "icon": "fa-gears",
            },
            {
                "name": "Max Budget",
                "count": header_with_tooltip(
                    f"{object.currency} {intcomma(object.total_budget)}",
                    "Maximum payments that can be made for workers and organization",
                ),
                "icon": "fa-money-bill",
            },
        ]
        context["export_form"] = PaymentExportForm()
        context["export_task_id"] = request.GET.get("export_task_id")
        return context


@org_member_required
def export_user_visits(request, org_slug, opp_id):
    get_opportunity_or_404(org_slug=request.org.slug, pk=opp_id)
    form = VisitExportForm(data=request.POST)
    if not form.is_valid():
        messages.error(request, form.errors)
        return redirect("opportunity:worker_list", request.org.slug, opp_id)

    export_format = form.cleaned_data["format"]
    date_range = DateRanges(form.cleaned_data["date_range"])
    status = form.cleaned_data["status"]
    flatten = form.cleaned_data["flatten_form_data"]
    result = generate_visit_export.delay(opp_id, date_range, status, export_format, flatten)
    redirect_url = reverse("opportunity:worker_deliver", args=(request.org.slug, opp_id))
    return redirect(f"{redirect_url}?export_task_id={result.id}")


@org_member_required
def review_visit_export(request, org_slug, opp_id):
    get_opportunity_or_404(org_slug=request.org.slug, pk=opp_id)
    form = ReviewVisitExportForm(data=request.POST)
    redirect_url = reverse("opportunity:worker_deliver", args=(org_slug, opp_id))
    if not form.is_valid():
        messages.error(request, form.errors)
        return redirect(redirect_url)

    export_format = form.cleaned_data["format"]
    date_range = DateRanges(form.cleaned_data["date_range"])
    status = form.cleaned_data["status"]

    result = generate_review_visit_export.delay(opp_id, date_range, status, export_format)
    return redirect(f"{redirect_url}?export_task_id={result.id}")


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
        "components/upload_progress_bar.html",
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
def update_visit_status_import(request, org_slug=None, opp_id=None):
    opportunity = get_opportunity_or_404(org_slug=org_slug, pk=opp_id)
    file = request.FILES.get("visits")
    file_format = get_file_extension(file)
    redirect_url = reverse("opportunity:worker_deliver", args=(org_slug, opp_id))

    if file_format not in ("csv", "xlsx"):
        messages.error(request, f"Invalid file format. Only 'CSV' and 'XLSX' are supported. Got {file_format}")
    else:
        file_path = f"{opportunity.pk}_{datetime.datetime.now().isoformat}_visit_import"
        saved_path = default_storage.save(file_path, file)
        result = bulk_update_visit_status_task.delay(opportunity.pk, saved_path, file_format)
        redirect_url = f"{redirect_url}?export_task_id={result.id}"
    return redirect(redirect_url)


def review_visit_import(request, org_slug=None, opp_id=None):
    opportunity = get_opportunity_or_404(org_slug=org_slug, pk=opp_id)
    file = request.FILES.get("visits")
    redirect_url = reverse("opportunity:worker_deliver", args=(org_slug, opp_id))
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
def add_budget_existing_users(request, org_slug=None, opp_id=None):
    opportunity = get_opportunity_or_404(org_slug=org_slug, pk=opp_id)
    opportunity_access = OpportunityAccess.objects.filter(opportunity=opportunity)
    opportunity_claims = OpportunityClaim.objects.filter(opportunity_access__in=opportunity_access).annotate(
        total_max_visits=Coalesce(Sum("opportunityclaimlimit__max_visits"), Value(0))
    )

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
        return redirect("opportunity:add_budget_existing_users", org_slug, opp_id)

    tabs = [
        {
            "key": "existing_workers",
            "label": "Existing Connect Workers",
        },
    ]
    # Nm are not allowed to increase the managed opportunity budget so do not provide that tab.
    if not opportunity.managed or request.is_opportunity_pm:
        tabs.append(
            {
                "key": "new_workers",
                "label": "New Connect Workers",
            }
        )

    path = [
        {"title": "Opportunities", "url": reverse("opportunity:list", args=(request.org.slug,))},
        {"title": opportunity.name, "url": reverse("opportunity:detail", args=(request.org.slug, opportunity.pk))},
        {
            "title": "Add budget",
        },
    ]

    return render(
        request,
        "opportunity/add_visits_existing_users.html",
        {
            "form": form,
            "tabs": tabs,
            "path": path,
            "opportunity_claims": opportunity_claims,
            "budget_per_visit": opportunity.budget_per_visit,
            "opportunity": opportunity,
        },
    )


@org_member_required
def add_budget_new_users(request, org_slug=None, opp_id=None):
    opportunity = get_opportunity_or_404(org_slug=org_slug, pk=opp_id)
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
            request, f"{opportunity.currency} {abs(form.budget_increase)} was {direction} the opportunity budget."
        )

        redirect_url = reverse("opportunity:add_budget_existing_users", args=[org_slug, opp_id])
        redirect_url += "?active_tab=new_users"
        response = HttpResponse()
        response["HX-Redirect"] = redirect_url
        return response

    csrf_token = get_token(request)
    form_html = f"""
        <form id="form-content"
              hx-post="{reverse('opportunity:add_budget_new_users', args=[org_slug, opp_id])}"
              hx-trigger="submit"
              hx-headers='{{"X-CSRFToken": "{csrf_token}"}}'>
            <input type="hidden" name="csrfmiddlewaretoken" value="{csrf_token}">
            {render_crispy_form(form)}
        </form>
        """

    return HttpResponse(mark_safe(form_html))


@org_member_required
def export_users_for_payment(request, org_slug, opp_id):
    get_opportunity_or_404(org_slug=request.org.slug, pk=opp_id)
    form = PaymentExportForm(data=request.POST)
    if not form.is_valid():
        messages.error(request, form.errors)
        return redirect("opportunity:worker_payments", org_slug, opp_id)

    export_format = form.cleaned_data["format"]
    result = generate_payment_export.delay(opp_id, export_format)
    redirect_url = reverse("opportunity:worker_payments", args=(request.org.slug, opp_id))
    return redirect(f"{redirect_url}?export_task_id={result.id}")


@org_member_required
@require_POST
def payment_import(request, org_slug=None, opp_id=None):
    opportunity = get_opportunity_or_404(org_slug=org_slug, pk=opp_id)
    file = request.FILES.get("payments")
    file_format = get_file_extension(file)
    if file_format not in ("csv", "xlsx"):
        raise ImportException(f"Invalid file format. Only 'CSV' and 'XLSX' are supported. Got {file_format}")

    file_path = f"{opportunity.pk}_{datetime.datetime.now().isoformat}_payment_import"
    saved_path = default_storage.save(file_path, file)
    result = bulk_update_payments_task.delay(opportunity.pk, saved_path, file_format)
    redirect_url = reverse("opportunity:worker_payments", args=(org_slug, opp_id))
    return redirect(f"{redirect_url}?export_task_id={result.id}")


@org_member_required
def add_payment_units(request, org_slug=None, opp_id=None):
    if request.POST:
        return add_payment_unit(request, org_slug=org_slug, opp_id=opp_id)
    opportunity = get_opportunity_or_404(org_slug=org_slug, pk=opp_id)
    paymentunit_count = PaymentUnit.objects.filter(opportunity=opportunity).count()
    return render(
        request,
        "opportunity/add_payment_units.html",
        dict(opportunity=opportunity, paymentunit_count=paymentunit_count),
    )


@org_member_required
def add_payment_unit(request, org_slug=None, opp_id=None):
    opportunity = get_opportunity_or_404(org_slug=org_slug, pk=opp_id)
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
        return redirect("opportunity:add_payment_units", org_slug=request.org.slug, opp_id=opportunity.id)
    elif request.POST:
        messages.error(request, "Invalid Data")
        return redirect("opportunity:add_payment_units", org_slug=request.org.slug, opp_id=opportunity.id)

    path = [
        {"title": "Opportunities", "url": reverse("opportunity:list", args=(request.org.slug,))},
        {"title": opportunity.name, "url": reverse("opportunity:detail", args=(request.org.slug, opportunity.pk))},
        {
            "title": "Payment unit",
        },
    ]
    return render(
        request,
        "components/partial_form.html" if request.GET.get("partial") == "True" else "components/form.html",
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
        return redirect("opportunity:finalize", org_slug=request.org.slug, opp_id=opportunity.id)

    path = [
        {"title": "Opportunities", "url": reverse("opportunity:list", args=(request.org.slug,))},
        {"title": opportunity.name, "url": reverse("opportunity:detail", args=(request.org.slug, opportunity.pk))},
        {
            "title": "Payment unit",
        },
    ]
    return render(
        request,
        "components/form.html",
        dict(title=f"{request.org.slug} - {opportunity.name}", form_title="Payment Unit Edit", form=form, path=path),
    )


@org_member_required
def export_user_status(request, org_slug, opp_id):
    get_opportunity_or_404(org_slug=request.org.slug, pk=opp_id)
    form = PaymentExportForm(data=request.POST)
    if not form.is_valid():
        messages.error(request, form.errors)
        return redirect("opportunity:worker_list", request.org.slug, opp_id)

    export_format = form.cleaned_data["format"]
    result = generate_user_status_export.delay(opp_id, export_format)
    redirect_url = reverse("opportunity:worker_list", args=(request.org.slug, opp_id))
    return redirect(f"{redirect_url}?export_task_id={result.id}")


@org_member_required
def export_deliver_status(request, org_slug, opp_id):
    get_opportunity_or_404(pk=opp_id, org_slug=request.org.slug)
    form = PaymentExportForm(data=request.POST)
    if not form.is_valid():
        messages.error(request, form.errors)
        return redirect("opportunity:detail", request.org.slug, opp_id)

    export_format = form.cleaned_data["format"]
    result = generate_deliver_status_export.delay(opp_id, export_format)
    redirect_url = reverse("opportunity:detail", args=(request.org.slug, opp_id))
    return redirect(f"{redirect_url}?export_task_id={result.id}")


@org_member_required
@require_POST
def payment_delete(request, org_slug=None, opp_id=None, access_id=None, pk=None):
    opportunity = get_opportunity_or_404(pk=opp_id, org_slug=org_slug)
    opportunity_access = get_object_or_404(OpportunityAccess, pk=access_id, opportunity=opportunity)
    payment = get_object_or_404(Payment, opportunity_access=opportunity_access, pk=pk)
    payment.delete()
    return redirect("opportunity:worker_payments", org_slug, opp_id)


@org_admin_required
def send_message_mobile_users(request, org_slug=None, opp_id=None):
    opportunity = get_opportunity_or_404(pk=opp_id, org_slug=org_slug)
    user_ids = OpportunityAccess.objects.filter(opportunity=opportunity, accepted=True).values_list(
        "user_id", flat=True
    )
    users = User.objects.filter(pk__in=user_ids)
    form = SendMessageMobileUsersForm(users=users, data=request.POST or None)

    if form.is_valid():
        selected_user_ids = form.cleaned_data["selected_users"]
        title = form.cleaned_data["title"]
        body = form.cleaned_data["body"]
        send_push_notification_task.delay(selected_user_ids, title, body)

        return redirect("opportunity:detail", org_slug=request.org.slug, opp_id=opp_id)

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


@org_member_required
@require_POST
def approve_visits(request, org_slug, opp_id):
    visit_ids = request.POST.getlist("visit_ids[]")

    visits = (
        UserVisit.objects.filter(id__in=visit_ids, opportunity_id=opp_id)
        .filter(~Q(status=VisitValidationStatus.approved) | Q(review_status=VisitReviewStatus.disagree))
        .prefetch_related("opportunity")
        .only("status", "review_status", "flagged", "justification", "review_created_on")
    )

    if len({visit.user_id for visit in visits}) > 1:
        return HttpResponseBadRequest(
            "All visits must belong to the same user.",
            headers={"HX-Trigger": "form_error"},
        )

    today = now()
    for visit in visits:
        visit.status = VisitValidationStatus.approved
        if visit.opportunity.managed:
            visit.review_created_on = today
            if visit.review_status == VisitReviewStatus.disagree:
                visit.review_status = VisitReviewStatus.pending
            if visit.flagged:
                justification = request.POST.get("justification")
                if not justification:
                    return HttpResponse(
                        "Justification is mandatory for flagged visits.",
                        status=400,
                        headers={"HX-Trigger": "form_error"},
                    )
                visit.justification = justification

    UserVisit.objects.bulk_update(visits, ["status", "review_created_on", "review_status", "justification"])
    if visits:
        update_payment_accrued(opportunity=visits[0].opportunity, users=[visits[0].user], incremental=True)

    return HttpResponse(status=200, headers={"HX-Trigger": "reload_table"})


@org_member_required
@require_POST
def reject_visits(request, org_slug=None, opp_id=None):
    opp = get_opportunity_or_404(opp_id, org_slug)
    visit_ids = request.POST.getlist("visit_ids[]")
    reason = request.POST.get("reason", "").strip()

    UserVisit.objects.filter(id__in=visit_ids, opportunity_id=opp_id).exclude(
        status=VisitValidationStatus.rejected
    ).update(status=VisitValidationStatus.rejected, reason=reason)
    if visit_ids:
        visit = UserVisit.objects.get(id=visit_ids[0])
        update_payment_accrued(opportunity=opp, users=[visit.user])
    return HttpResponse(status=200, headers={"HX-Trigger": "reload_table"})


@org_member_required
def fetch_attachment(self, org_slug, blob_id):
    blob_meta = BlobMeta.objects.get(blob_id=blob_id)
    attachment = storages["default"].open(blob_id)
    return FileResponse(attachment, filename=blob_meta.name, content_type=blob_meta.content_type)


@org_member_required
def verification_flags_config(request, org_slug=None, opp_id=None):
    opportunity = get_opportunity_or_404(pk=opp_id, org_slug=org_slug)
    if opportunity.managed and not request.is_opportunity_pm:
        return redirect("opportunity:detail", org_slug=org_slug, opp_id=opp_id)
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
        opportunity_id = self.kwargs["opp_id"]
        org_slug = self.kwargs["org_slug"]
        opportunity = get_opportunity_or_404(org_slug=org_slug, pk=opportunity_id)
        access_objects = OpportunityAccess.objects.filter(opportunity=opportunity)
        return list(
            filter(lambda cw: cw.completed, CompletedWork.objects.filter(opportunity_access__in=access_objects))
        )


@org_member_required
def export_completed_work(request, org_slug, opp_id):
    get_opportunity_or_404(org_slug=request.org.slug, pk=opp_id)
    form = PaymentExportForm(data=request.POST)
    if not form.is_valid():
        messages.error(request, form.errors)
        return redirect("opportunity:detail", request.org.slug, opp_id)

    export_format = form.cleaned_data["format"]
    result = generate_work_status_export.delay(opp_id, export_format)
    redirect_url = reverse("opportunity:detail", args=(request.org.slug, opp_id))
    return redirect(f"{redirect_url}?export_task_id={result.id}")


@org_member_required
@require_POST
def update_completed_work_status_import(request, org_slug=None, opp_id=None):
    opportunity = get_opportunity_or_404(org_slug=org_slug, pk=opp_id)
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
    return redirect("opportunity:detail", org_slug, opp_id)


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
def suspended_users_list(request, org_slug=None, opp_id=None):
    opportunity = get_opportunity_or_404(org_slug=org_slug, pk=opp_id)
    access_objects = OpportunityAccess.objects.filter(opportunity=opportunity, suspended=True)
    table = SuspendedUsersTable(access_objects)
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
            {"title": "Suspended Users", "url": request.path},
        ]
    )
    return render(request, "opportunity/suspended_users.html", dict(table=table, opportunity=opportunity, path=path))


@org_member_required
def export_catchment_area(request, org_slug, opp_id):
    get_opportunity_or_404(org_slug=request.org.slug, pk=opp_id)
    form = PaymentExportForm(data=request.POST)
    if not form.is_valid():
        messages.error(request, form.errors)
        return redirect("opportunity:detail", request.org.slug, opp_id)

    export_format = form.cleaned_data["format"]
    result = generate_catchment_area_export.delay(opp_id, export_format)
    redirect_url = reverse("opportunity:detail", args=(request.org.slug, opp_id))
    return redirect(f"{redirect_url}?export_task_id={result.id}")


@org_member_required
@require_POST
def import_catchment_area(request, org_slug=None, opp_id=None):
    opportunity = get_opportunity_or_404(org_slug=org_slug, pk=opp_id)
    file = request.FILES.get("catchments")
    try:
        status = bulk_update_catchments(opportunity, file)
    except ImportException as e:
        messages.error(request, e.message)
    else:
        message = f"{len(status)} catchment areas were updated successfully and {status.new_catchments} were created."
        messages.success(request, mark_safe(message))
    return redirect("opportunity:detail", org_slug, opp_id)


@org_member_required
def opportunity_user_invite(request, org_slug=None, opp_id=None):
    opportunity = get_opportunity_or_404(org_slug=request.org.slug, pk=opp_id)
    form = OpportunityUserInviteForm(data=request.POST or None, opportunity=opportunity)
    if form.is_valid():
        users = form.cleaned_data["users"]
        if users:
            add_connect_users.delay(users, opportunity.id)
        return redirect("opportunity:detail", request.org.slug, opp_id)
    return render(
        request,
        "components/form.html",
        dict(title=f"{request.org.slug} - {opportunity.name}", form_title="Invite Connect Workers", form=form),
    )


@org_member_required
def user_visit_review(request, org_slug, opp_id):
    opportunity = get_opportunity_or_404(opp_id, org_slug)
    if request.POST and request.is_opportunity_pm:
        review_status = request.POST.get("review_status").lower()
        updated_reviews = request.POST.getlist("pk")
        user_visits = UserVisit.objects.filter(pk__in=updated_reviews)
        if review_status in [VisitReviewStatus.agree.value, VisitReviewStatus.disagree.value]:
            user_visits.update(review_status=review_status)
            update_payment_accrued(opportunity=opportunity, users=[visit.user for visit in user_visits])

    return HttpResponse(status=200, headers={"HX-Trigger": "reload_table"})


@org_member_required
def payment_report(request, org_slug, opp_id):
    opportunity = get_opportunity_or_404(opp_id, org_slug)
    usd = request.GET.get("usd", False)

    if not opportunity.managed:
        return redirect("opportunity:detail", org_slug, opp_id)

    amount_field = "amount"
    currency = opportunity.currency
    if usd:
        amount_field = "amount_usd"
        currency = "USD"

    total_paid_users = Payment.objects.filter(
        opportunity_access__opportunity=opportunity, organization__isnull=True
    ).aggregate(total=Sum(amount_field))["total"] or Decimal("0.00")
    total_paid_nm = Payment.objects.filter(
        organization=opportunity.organization, invoice__opportunity=opportunity
    ).aggregate(total=Sum(amount_field))["total"] or Decimal("0.00")
    data, total_user_payment_accrued, total_nm_payment_accrued = get_payment_report_data(opportunity, usd)
    table = PaymentReportTable(data)
    RequestConfig(request, paginate={"per_page": get_validated_page_size(request)}).configure(table)

    def render_amount(amount):
        return f"{currency} {intcomma(amount or 0)}"

    cards = [
        {
            "amount": render_amount(total_user_payment_accrued),
            "icon": "fa-user-friends",
            "label": "Connect Worker",
            "subtext": "Total Accrued",
        },
        {
            "amount": render_amount(total_paid_users),
            "icon": "fa-user-friends",
            "label": "Connect Worker",
            "subtext": "Total Paid",
        },
        {
            "amount": render_amount(total_nm_payment_accrued),
            "icon": "fa-building",
            "label": "Organization",
            "subtext": "Total Accrued",
        },
        {
            "amount": render_amount(total_paid_nm),
            "icon": "fa-building",
            "label": "Organization",
            "subtext": "Total Paid",
        },
    ]

    return render(
        request,
        "opportunity/invoice_payment_report.html",
        context=dict(
            table=table,
            opportunity=opportunity,
            cards=cards,
        ),
    )


@org_member_required
def invoice_list(request, org_slug, opp_id):
    opportunity = get_opportunity_or_404(opp_id, org_slug)
    if not opportunity.managed:
        return redirect("opportunity:detail", org_slug, opp_id)

    filter_kwargs = dict(opportunity=opportunity)

    queryset = PaymentInvoice.objects.filter(**filter_kwargs).order_by("date")
    csrf_token = get_token(request)

    table = PaymentInvoiceTable(
        queryset,
        org_slug=org_slug,
        opportunity=opportunity,
        exclude=("actions",) if not request.is_opportunity_pm else tuple(),
        csrf_token=csrf_token,
    )

    form = PaymentInvoiceForm(opportunity=opportunity)
    RequestConfig(request, paginate={"per_page": get_validated_page_size(request)}).configure(table)
    return render(
        request,
        "opportunity/invoice_list.html",
        {
            "opportunity": opportunity,
            "table": table,
            "form": form,
            "path": [
                {"title": "Opportunities", "url": reverse("opportunity:list", args=(org_slug,))},
                {"title": opportunity.name, "url": reverse("opportunity:detail", args=(org_slug, opp_id))},
                {"title": "Invoices", "url": reverse("opportunity:invoice_list", args=(org_slug, opp_id))},
            ],
        },
    )


@org_member_required
def invoice_create(request, org_slug=None, opp_id=None):
    opportunity = get_opportunity_or_404(opp_id, org_slug)
    if not opportunity.managed or request.is_opportunity_pm:
        return redirect("opportunity:detail", org_slug, opp_id)
    form = PaymentInvoiceForm(data=request.POST or None, opportunity=opportunity)
    if request.POST and form.is_valid():
        form.save()
        form = PaymentInvoiceForm(opportunity=opportunity)
        redirect_url = reverse("opportunity:invoice_list", args=[org_slug, opp_id])
        response = HttpResponse(status=200)
        response["HX-Redirect"] = redirect_url
        return response
    return HttpResponse(render_crispy_form(form))


@org_member_required
@require_POST
def invoice_approve(request, org_slug, opp_id):
    opportunity = get_opportunity_or_404(opp_id, org_slug)
    if not opportunity.managed or not (request.org_membership and request.org_membership.is_program_manager):
        return redirect("opportunity:detail", org_slug, opp_id)
    invoice_ids = request.POST.getlist("pk")
    invoices = PaymentInvoice.objects.filter(opportunity=opportunity, pk__in=invoice_ids, payment__isnull=True)

    for invoice in invoices:
        payment = Payment(
            amount=invoice.amount,
            organization=opportunity.organization,
            amount_usd=invoice.amount_usd,
            invoice=invoice,
        )
        payment.save()
    return redirect("opportunity:invoice_list", org_slug, opp_id)


@org_member_required
@require_POST
@csrf_exempt
def delete_user_invites(request, org_slug, opp_id):
    invite_ids = request.POST.getlist("user_invite_ids")
    if not invite_ids:
        return HttpResponseBadRequest()

    user_invites = (
        UserInvite.objects.filter(id__in=invite_ids, opportunity_id=opp_id)
        .exclude(status=UserInviteStatus.accepted)
        .select_related("opportunity_access")
    )

    opportunity_access_ids = [invite.opportunity_access.id for invite in user_invites if invite.opportunity_access]
    deleted_count = user_invites.count()
    cannot_delete_count = len(invite_ids) - deleted_count
    user_invites.delete()
    OpportunityAccess.objects.filter(id__in=opportunity_access_ids).delete()
    if deleted_count > 0:
        messages.success(request, mark_safe(f"Successfully deleted {deleted_count} invite(s)."))
    if cannot_delete_count > 0:
        messages.warning(
            request,
            mark_safe(f"Cannot delete {cannot_delete_count} invite(s). Accepted invites cannot be deleted."),
        )

    redirect_url = reverse("opportunity:worker_list", args=(request.org.slug, opp_id))
    return HttpResponse(headers={"HX-Redirect": redirect_url})


@org_admin_required
@require_POST
def resend_user_invites(request, org_slug, opp_id):
    invite_ids = request.POST.getlist("user_invite_ids")
    if not invite_ids:
        return HttpResponseBadRequest()

    user_invites = UserInvite.objects.filter(id__in=invite_ids, opportunity_id=opp_id).select_related(
        "opportunity_access__user"
    )

    recent_invites = []
    accepted_invites = []
    not_found_phone_numbers = set()
    valid_phone_numbers = []
    for user_invite in user_invites:
        if user_invite.status == UserInviteStatus.accepted:
            accepted_invites.append(user_invite.phone_number)
            continue
        if user_invite.notification_date and (now() - user_invite.notification_date) < timedelta(days=1):
            recent_invites.append(user_invite.phone_number)
            continue
        if user_invite.status == UserInviteStatus.not_found:
            not_found_phone_numbers.add(user_invite.phone_number)
            continue
        valid_phone_numbers.append(user_invite.phone_number)

    resent_count = 0
    if valid_phone_numbers:
        users = User.objects.filter(phone_number__in=valid_phone_numbers)
        for user in users:
            access, _ = OpportunityAccess.objects.get_or_create(user=user, opportunity_id=opp_id)
            invite_user.delay(user.id, access.pk)
            resent_count += 1

    if not_found_phone_numbers:
        found_user_list = fetch_users(not_found_phone_numbers)
        for found_user in found_user_list:
            not_found_phone_numbers.remove(found_user.phone_number)
            update_user_and_send_invite(found_user, opp_id)
            resent_count += 1

    if resent_count > 0:
        messages.success(request, mark_safe(f"Successfully resent {resent_count} invite(s)."))
    if recent_invites:
        messages.warning(
            request,
            mark_safe(
                "The following invites were skipped, as they were sent in the "
                f"last 24 hours: {', '.join(recent_invites)}"
            ),
        )
    if not_found_phone_numbers:
        messages.warning(
            request,
            mark_safe(
                "The following invites were skipped, as they are not "
                f"registered on PersonalID: {', '.join(not_found_phone_numbers)}"
            ),
        )
    if accepted_invites:
        messages.warning(
            request,
            mark_safe(
                f"The following invites were skipped, as they have already accepted: {', '.join(accepted_invites)}"
            ),
        )

    redirect_url = reverse("opportunity:worker_list", args=(request.org.slug, opp_id))
    return HttpResponse(headers={"HX-Redirect": redirect_url})


def sync_deliver_units(request, org_slug, opp_id):
    status = HTTPStatus.OK
    message = "Delivery unit sync completed."
    try:
        create_learn_modules_and_deliver_units(opp_id)
    except AppNoBuildException:
        status = HTTPStatus.BAD_REQUEST
        message = "Failed to retrieve updates. No available build at the moment."

    return HttpResponse(content=message, status=status)


@org_viewer_required
def user_visit_verification(request, org_slug, opp_id, pk):
    opportunity = get_opportunity_or_404(opp_id, org_slug)
    opportunity_access = get_object_or_404(OpportunityAccess, opportunity=opportunity, pk=pk)

    user_visit_counts = get_user_visit_counts(opportunity_access_id=pk)
    visits = UserVisit.objects.filter(opportunity_access=opportunity_access, flagged=True, flag_reason__isnull=False)
    flagged_info = defaultdict(lambda: {"name": "", "approved": 0, "pending": 0, "rejected": 0})
    for visit in visits:
        for flag, _description in visit.flag_reason.get("flags", []):
            flag_label = FlagLabels.get_label(flag)
            if visit.status == VisitValidationStatus.approved:
                if opportunity.managed and visit.review_created_on is not None:
                    if visit.review_status == VisitReviewStatus.agree:
                        flagged_info[flag_label]["approved"] += 1
                    else:
                        flagged_info[flag_label]["pending"] += 1
                else:
                    flagged_info[flag_label]["approved"] += 1
            if visit.status in (VisitValidationStatus.pending, VisitValidationStatus.duplicate):
                flagged_info[flag_label]["pending"] += 1
            if visit.status == VisitValidationStatus.rejected:
                flagged_info[flag_label]["rejected"] += 1
            flagged_info[flag_label]["name"] = flag_label
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
            {
                "title": "Connect Workers",
                "url": reverse("opportunity:worker_deliver", args=(org_slug, opp_id)),
            },
            {"title": opportunity_access.user.name, "url": request.path},
        ]
    )

    response = render(
        request,
        "opportunity/user_visit_verification.html",
        context={
            "opportunity_access": opportunity_access,
            "counts": user_visit_counts,
            "flagged_info": flagged_info,
            "last_payment_details": last_payment_details,
            "MAPBOX_TOKEN": settings.MAPBOX_TOKEN,
            "opportunity": opportunity_access.opportunity,
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
                    status=VisitValidationStatus.approved,
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
        return get_validated_page_size(self.request)

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
        kwargs["is_opportunity_pm"] = self.request.is_opportunity_pm
        return kwargs

    def get_context_data(self, **kwargs):
        user_visit_counts = get_user_visit_counts(self.kwargs["pk"], self.filter_date)

        if self.request.is_opportunity_pm:
            tabs = [
                {
                    "name": "pending_review",
                    "label": "Pending PM Review",
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
                    "label": "Pending NM Review",
                    "count": user_visit_counts.get("pending", 0),
                }
            ]

            if self.opportunity.managed:
                dynamic_tabs = [
                    {
                        "name": "pending_review",
                        "label": "Pending PM Review",
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
                    "status": VisitValidationStatus.approved,
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


@org_viewer_required
def user_visit_details(request, org_slug, opp_id, pk):
    opportunity = get_opportunity_or_404(opp_id, org_slug)
    user_visit = get_object_or_404(UserVisit, pk=pk, opportunity=opportunity)
    verification_flags_config = opportunity.opportunityverificationflags

    serializer = XFormSerializer(data=user_visit.form_json)
    serializer.is_valid()
    xform = serializer.save()

    visit_data = {
        "entity_name": user_visit.entity_name,
        "user__name": user_visit.user.name,
        "status": user_visit.get_status_display(),
        "visit_date": user_visit.visit_date,
    }

    user_forms = []
    other_forms = []
    closest_distance = sys.maxsize

    if user_visit.location:
        lat, lon, _, precision = user_visit.location.split(" ")
        lat = float(lat)
        lon = float(lon)

        # Bounding box delta for 250m
        lat_delta = 0.00225
        lon_delta = 0.00225

        class SplitPart(Func):
            function = "SPLIT_PART"
            arity = 3

        # Fetch only points within 250m
        qs = (
            UserVisit.objects.filter(opportunity=opportunity)
            .exclude(pk=user_visit.pk)
            .annotate(
                lat_val=Cast(SplitPart("location", Value(" "), Value(1)), FloatField()),
                lon_val=Cast(SplitPart("location", Value(" "), Value(2)), FloatField()),
            )
            .filter(
                lat_val__range=(lat - lat_delta, lat + lat_delta),
                lon_val__range=(lon - lon_delta, lon + lon_delta),
            )
            .select_related("user")
        )

        for loc in qs:
            if not loc.location:
                continue
            try:
                other_lat, other_lon, *_ = loc.location.split()
                dist = distance.distance((lat, lon), (float(other_lat), float(other_lon))).m
                closest_distance = int(min(closest_distance, dist))
                if dist <= 250:
                    visit_info = {
                        "entity_name": loc.entity_name,
                        "user__name": loc.user.name,
                        "status": loc.get_status_display(),
                        "visit_date": loc.visit_date,
                        "url": reverse(
                            "opportunity:user_visit_details",
                            kwargs={"org_slug": request.org.slug, "opp_id": loc.opportunity_id, "pk": loc.pk},
                        ),
                    }
                    form = (visit_info, dist, other_lat, other_lon, precision)
                    if user_visit.user_id == loc.user_id:
                        user_forms.append(form)
                    else:
                        other_forms.append(form)
            except Exception:
                continue

        user_forms.sort(key=lambda x: x[1])
        other_forms.sort(key=lambda x: x[1])
        visit_data.update({"lat": lat, "lon": lon, "precision": precision})

    flags = []
    attachment_flagged = False
    if user_visit.flagged and user_visit.flag_reason:
        for flag, description in user_visit.flag_reason.get("flags", []):
            if flag == Flags.ATTACHMENT_MISSING.value:
                attachment_flagged = True
                continue
            flags.append((FlagLabels.get_label(flag), description))
    flag_count = len(flags) + attachment_flagged

    return render(
        request,
        "opportunity/user_visit_details.html",
        context=dict(
            user_visit=user_visit,
            xform=xform,
            user_forms=user_forms[:5],
            other_forms=other_forms[:5],
            visit_data=visit_data,
            closest_distance=closest_distance,
            verification_flags_config=verification_flags_config,
            flags=flags,
            flag_count=flag_count,
            attachment_flagged=attachment_flagged,
        ),
    )


class BaseWorkerListView(OrganizationUserMixin, OpportunityObjectMixin, View):
    template_name = "opportunity/opportunity_worker.html"
    hx_template_name = "opportunity/workers.html"
    active_tab = "workers"
    tabs = [
        {"key": "workers", "label": "Connect Workers", "url_name": "opportunity:worker_list"},
        {"key": "learn", "label": "Learn", "url_name": "opportunity:worker_learn"},
        {"key": "deliver", "label": "Deliver", "url_name": "opportunity:worker_deliver"},
        {"key": "payments", "label": "Payments", "url_name": "opportunity:worker_payments"},
    ]

    def _is_navigating_between_tabs(self, org_slug, opportunity):
        referer = self.request.headers.get("referer")
        is_tab_navigation = False
        if referer:
            path = urlparse(referer).path
            for tab in self.tabs:
                if path.endswith(reverse(tab["url_name"], args=(org_slug, opportunity.id))):
                    is_tab_navigation = True
                    break
        return is_tab_navigation

    def get_tabs(self, org_slug, opportunity):
        tabs_with_urls = []
        # Persist url-params when navigating in between tabs, but not other pages
        is_tab_navigation = self._is_navigating_between_tabs(org_slug, opportunity)
        session_key_prefix = "worker_tab_params"

        params = {}
        if not is_tab_navigation:
            # Clear url params
            for key in [t["key"] for t in self.tabs]:
                self.request.session.pop(f"{session_key_prefix}:{key}", None)
        if self.request.GET:
            # Save url params
            params = self.request.GET.dict()
            self.request.session[f"{session_key_prefix}:{self.active_tab}"] = params
        elif is_tab_navigation:
            # Persist
            params = self.request.session.get(f"{session_key_prefix}:{self.active_tab}", {})

        # build urls with params
        for tab in self.tabs:
            url = reverse(tab["url_name"], args=(org_slug, opportunity.id))
            if tab["key"] == self.active_tab:
                tab_params = params
            else:
                tab_params = self.request.session.get(f"worker_tab_params:{tab['key']}", {})
            if tab_params:
                url = f"{url}?{urlencode(tab_params)}"
            tabs_with_urls.append({**tab, "url": url})

        # Label with count for workers tab
        workers_count = (
            UserInvite.objects.filter(opportunity=opportunity).exclude(status=UserInviteStatus.not_found).count()
        )
        tabs_with_urls[0]["label"] = f"Connect Workers ({workers_count})"
        return tabs_with_urls

    def get(self, request, org_slug, opp_id):
        opportunity = self.get_opportunity()
        context = self.get_context_data(opportunity, org_slug)
        context.update(self.get_extra_context(opportunity, org_slug))
        return render(
            request,
            self.hx_template_name if request.htmx else self.template_name,
            context,
        )

    def get_context_data(self, opportunity, org_slug):
        path = []
        if opportunity.managed:
            path.append({"title": "Programs", "url": reverse("program:home", args=(org_slug,))})
            path.append(
                {
                    "title": opportunity.program_name,
                    "url": reverse("program:home", args=(org_slug,)),
                }
            )
        path.extend(
            [
                {"title": "Opportunities", "url": reverse("opportunity:list", args=(org_slug,))},
                {"title": opportunity.name, "url": reverse("opportunity:detail", args=(org_slug, opportunity.id))},
                {
                    "title": "Connect Workers",
                    "url": reverse("opportunity:worker_list", args=(org_slug, opportunity.id)),
                },
            ]
        )

        context = {
            "path": path,
            "opportunity": opportunity,
            "active_tab": self.active_tab,
            "tabs": self.get_tabs(org_slug, opportunity),
            "export_task_id": self.request.GET.get("export_task_id"),
        }
        if self.request.htmx:
            context["table"] = self.get_table(opportunity, org_slug)
        return context

    def get_extra_context(self, opportunity, org_slug):
        return {}

    def get_table(self, opportunity, org_slug):
        raise NotImplementedError


class WorkerView(BaseWorkerListView):
    hx_template_name = "opportunity/workers.html"
    active_tab = "workers"

    def get_extra_context(self, opportunity, org_slug):
        return {"export_form": PaymentExportForm()}

    def get_table(self, opportunity, org_slug):
        data = get_worker_table_data(opportunity)
        table = WorkerStatusTable(data)
        RequestConfig(self.request, paginate={"per_page": get_validated_page_size(self.request)}).configure(table)
        return table


class WorkerLearnView(BaseWorkerListView):
    hx_template_name = "opportunity/learn.html"
    active_tab = "learn"

    def get_table(self, opportunity, org_slug):
        data = get_worker_learn_table_data(opportunity)
        table = WorkerLearnTable(data, org_slug=org_slug, opp_id=opportunity.id)
        RequestConfig(self.request, paginate={"per_page": get_validated_page_size(self.request)}).configure(table)
        return table


class WorkerDeliverView(BaseWorkerListView, FilterMixin):
    hx_template_name = "opportunity/deliver.html"
    active_tab = "deliver"
    filter_class = DeliverFilterSet

    def get_extra_context(self, opportunity, org_slug):
        context = {
            "visit_export_form": VisitExportForm(),
            "review_visit_export_form": ReviewVisitExportForm(),
            "import_export_delivery_urls": {
                "export_url_for_pm": reverse(
                    "opportunity:review_visit_export",
                    args=(org_slug, opportunity.id),
                ),
                "export_url_for_nm": reverse(
                    "opportunity:visit_export",
                    args=(org_slug, opportunity.id),
                ),
                "import_url": reverse(
                    "opportunity:review_visit_import"
                    if (opportunity.managed and self.request.is_opportunity_pm)
                    else "opportunity:visit_import",
                    args=(org_slug, opportunity.id),
                ),
            },
            "import_visit_helper_text": _(
                'The file must contain at least the "Visit ID"{extra} and "Status" column. The import is case-insensitive.'  # noqa: E501
            ).format(extra=_(', "Justification"') if opportunity.managed else ""),
            "export_user_visit_title": _(
                "Import PM Review Sheet"
                if (opportunity.managed and self.request.is_opportunity_pm)
                else "Import Verified Visits"
            ),
        }
        context.update(self.get_filter_context())
        return context

    def get_table(self, opportunity, org_slug):
        data = get_annotated_opportunity_access_deliver_status(opportunity, self.get_filter_values())
        table = WorkerDeliveryTable(data, org_slug=org_slug, opp_id=opportunity.id)
        RequestConfig(self.request, paginate={"per_page": get_validated_page_size(self.request)}).configure(table)
        return table


class WorkerPaymentsView(BaseWorkerListView):
    hx_template_name = "opportunity/payments.html"
    active_tab = "payments"

    def get_extra_context(self, opportunity, org_slug):
        return {"export_form": PaymentExportForm()}

    def get_table(self, opportunity, org_slug):
        def get_payment_subquery(confirmed: bool = False) -> Subquery:
            qs = Payment.objects.filter(opportunity_access=OuterRef("pk"))
            if confirmed:
                qs = qs.filter(confirmed=True)
            subquery = qs.values("opportunity_access").annotate(total=Sum("amount")).values("total")[:1]
            return Coalesce(Subquery(subquery), Value(0), output_field=DecimalField())

        query_set = OpportunityAccess.objects.filter(
            opportunity=opportunity, payment_accrued__gte=0, accepted=True
        ).order_by("-payment_accrued")
        query_set = query_set.annotate(
            last_paid=Max("payment__date_paid"),
            total_paid_d=get_payment_subquery(),
            confirmed_paid_d=get_payment_subquery(True),
        )
        table = WorkerPaymentsTable(query_set, org_slug=org_slug, opp_id=opportunity.id)
        RequestConfig(self.request, paginate={"per_page": get_validated_page_size(self.request)}).configure(table)
        return table


@org_viewer_required
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
        "opportunity/opportunity_worker_learn.html",
        {"total_learn_duration": total_duration, "table": table, "access": access},
    )


@org_viewer_required
def worker_payment_history(request, org_slug, opp_id, access_id):
    access = get_object_or_404(OpportunityAccess, opportunity__id=opp_id, pk=access_id)
    queryset = Payment.objects.filter(opportunity_access=access).order_by("-date_paid")
    payments = queryset.values("date_paid", "amount")

    return render(
        request,
        "components/worker_page/payment_history.html",
        context=dict(access=access, payments=payments, latest_payment=queryset.first()),
    )


@org_viewer_required
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

    completed_work_ids = visits.values_list("completed_work_id", flat=True)
    duplicate_count = CompletedWork.objects.filter(id__in=completed_work_ids, saved_completed_count__gt=1).count()
    if duplicate_count:
        counts["Duplicate"] = duplicate_count

    return render(
        request,
        "components/worker_page/flag_counts.html",
        context=dict(
            flag_counts=counts.items(),
        ),
    )


@org_viewer_required
def learn_module_table(request, org_slug=None, opp_id=None):
    opp = get_opportunity_or_404(opp_id, org_slug)
    data = LearnModule.objects.filter(app=opp.learn_app)
    table = LearnModuleTable(data)
    return render(request, "tables/single_table.html", {"table": table})


@org_viewer_required
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
        opportunity_id = self.kwargs["opp_id"]
        org_slug = self.kwargs["org_slug"]
        self.opportunity = get_opportunity_or_404(org_slug=org_slug, pk=opportunity_id)
        return PaymentUnit.objects.filter(opportunity=self.opportunity).prefetch_related("deliver_units")

    def get_table_kwargs(self):
        kwargs = super().get_table_kwargs()
        kwargs["org_slug"] = self.request.org.slug
        program_manager = self.request.is_opportunity_pm
        kwargs["can_edit"] = (
            not self.opportunity.managed and self.request.org_membership and not self.request.org_membership.is_viewer
        ) or program_manager
        if self.opportunity.managed:
            kwargs["org_pay_per_visit"] = self.opportunity.org_pay_per_visit
        return kwargs


@org_viewer_required
def opportunity_funnel_progress(request, org_slug, opp_id):
    result = get_opportunity_funnel_progress(opp_id)

    accepted = result.workers_invited - result.pending_invites

    funnel_progress = [
        {
            "stage": "Invited",
            "count": header_with_tooltip(
                result.workers_invited,
                "Number of phone numbers to whom an SMS or push notification was sent and ConnectID exists",
            ),
            "icon": "envelope",
        },
        {
            "stage": "Accepted",
            "count": header_with_tooltip(
                accepted, "Connect Workers that have clicked on the SMS or push notification or gone into Learn app"
            ),
            "icon": "circle-check",
        },
        {
            "stage": "Started Learning",
            "count": header_with_tooltip(result.started_learning_count, "Started download of the Learn app"),
            "icon": "book-open",
        },
        {
            "stage": "Completed Learning",
            "count": header_with_tooltip(
                result.completed_learning, "Connect Workers that have completed all Learn modules but not assessment"
            ),
            "icon": "book",
        },
        {
            "stage": "Completed Assessment",
            "count": header_with_tooltip(result.completed_assessments, "Connect Workers that passed the assessment"),
            "icon": "award",
        },
        {
            "stage": "Claimed Job",
            "count": header_with_tooltip(
                result.claimed_job,
                "Connect Workers that have read the Opportunity terms and started download of the Deliver app",
            ),
            "icon": "user-check",
        },
        {
            "stage": "Started Delivery",
            "count": header_with_tooltip(
                result.started_deliveries, "Connect Workers that have submitted at least 1 Learn form"
            ),
            "icon": "house-chimney-user",
        },
    ]

    return render(
        request,
        "opportunity/opportunity_funnel_progress.html",
        {"funnel_progress": funnel_progress},
    )


@org_viewer_required
def opportunity_worker_progress(request, org_slug, opp_id):
    result = get_opportunity_worker_progress(opp_id)

    def safe_percent(numerator, denominator):
        percent = (numerator / denominator) * 100 if denominator else 0
        return 100 if percent > 100 else percent

    verified_percentage = safe_percent(result.approved_deliveries or 0, result.total_deliveries or 0)
    rejected_percentage = safe_percent(result.rejected_deliveries or 0, result.total_deliveries or 0)
    earned_percentage = safe_percent(result.total_accrued or 0, result.total_budget or 0)
    paid_percentage = safe_percent(result.total_paid or 0, result.total_accrued or 0)

    def amount_with_currency(amount):
        return f"{result.currency + ' ' if result.currency else ''}{intcomma(amount or 0)}"

    worker_progress = [
        {
            "title": "Verification",
            "progress": [
                {
                    "title": "Approved",
                    "total": header_with_tooltip(
                        result.approved_deliveries,
                        "Number of Service Deliveries Approved by both PM and NM or Auto-approved",
                    ),
                    "value": header_with_tooltip(
                        f"{verified_percentage:.0f}%", "Percentage Approved out of Delivered"
                    ),
                    "badge_type": True,
                    "percent": verified_percentage,
                },
                {
                    "title": "Rejected",
                    "total": header_with_tooltip(result.rejected_deliveries, "Number of Service Deliveries Rejected"),
                    "value": header_with_tooltip(
                        f"{rejected_percentage:.0f}%", "Percentage Rejected out of Delivered"
                    ),
                    "badge_type": True,
                    "percent": rejected_percentage,
                },
            ],
        },
        {
            "title": "Payments to Connect Workers",
            "progress": [
                {
                    "title": "Earned",
                    "total": header_with_tooltip(amount_with_currency(result.total_accrued), "Earned Amount"),
                    "value": header_with_tooltip(
                        f"{earned_percentage:.0f}%",
                        "Percentage Earned by all workers out of Max Budget in the Opportunity",
                    ),
                    "badge_type": True,
                    "percent": earned_percentage,
                },
                {
                    "title": "Paid",
                    "total": header_with_tooltip(
                        amount_with_currency(result.total_paid), "Paid Amount to All Connect Workers"
                    ),
                    "value": header_with_tooltip(
                        f"{paid_percentage:.0f}%", "Percentage Paid to all  workers out of Earned amount"
                    ),
                    "badge_type": True,
                    "percent": paid_percentage,
                },
            ],
        },
    ]

    return render(
        request,
        "opportunity/opportunity_worker_progress.html",
        {"worker_progress": worker_progress},
    )


@org_viewer_required
def opportunity_delivery_stats(request, org_slug, opp_id):
    panel_type_2 = {
        "body": "bg-brand-marigold/10 border border-brand-marigold",
        "icon_bg": "!bg-orange-300",
        "text_color": "!text-orange-500",
    }

    opportunity = get_opportunity_or_404(opp_id, org_slug)

    stats = get_opportunity_delivery_progress(opportunity.id)

    worker_list_url = reverse("opportunity:worker_list", args=(org_slug, opp_id))
    status_url = f"{worker_list_url}?{urlencode({'sort': '-last_active'})}"
    delivery_url = reverse("opportunity:worker_deliver", args=(org_slug, opp_id))
    payment_url = reverse("opportunity:worker_payments", args=(org_slug, opp_id))

    deliveries_panels = [
        {
            "icon": "fa-clipboard-list",
            "name": "Services Delivered",
            "status": "Total",
            "value": header_with_tooltip(stats.total_deliveries, "Total delivered so far excluding duplicates"),
            "url": f"{delivery_url}?{urlencode({'sort': '-last_active'})}",
            "incr": stats.deliveries_from_yesterday,
        },
        {
            "icon": "fa-clipboard-list",
            "name": "Services Delivered",
            "status": "Pending NM Review",
            "value": header_with_tooltip(
                stats.flagged_deliveries_waiting_for_review, "Flagged and pending review with NM"
            ),
            "url": f"{delivery_url}?{urlencode({'review_pending': 'True'})}",
            "incr": stats.flagged_deliveries_waiting_for_review_since_yesterday,
        },
    ]

    if opportunity.managed:
        deliveries_panels.append(
            {
                "icon": "fa-clipboard-list",
                "name": "Services Delivered",
                "status": "Pending PM Review",
                "url": f"{delivery_url}?{urlencode({'review_pending': 'True'})}",
                "value": header_with_tooltip(stats.visits_pending_for_pm_review, "Flagged and pending review with PM"),
                "incr": stats.visits_pending_for_pm_review_since_yesterday,
            }
        )

    opp_stats = [
        {
            "title": "Connect Workers",
            "sub_heading": "",
            "value": "",
            "panels": [
                {
                    "icon": "fa-user-group",
                    "name": "Connect Workers",
                    "status": "Invited",
                    "value": stats.workers_invited,
                    "url": status_url,
                },
                {
                    "icon": "fa-user-check",
                    "name": "Connect Workers",
                    "status": "Yet to Accept Invitation",
                    "value": stats.pending_invites,
                },
                {
                    "icon": "fa-clipboard-list",
                    "name": "Connect Workers",
                    "status": "Inactive last 3 days",
                    "url": f"{delivery_url}?{urlencode({'last_active': '3'})}",
                    "value": header_with_tooltip(
                        stats.inactive_workers, "Did not submit a Learn or Deliver form in the last 3 days"
                    ),
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
                    "value": header_with_tooltip(
                        intcomma(stats.total_accrued), "Worker payment accrued based on approved service deliveries"
                    ),
                    "url": payment_url,
                    "incr": stats.accrued_since_yesterday,
                },
                {
                    "icon": "fa-hand-holding-droplet",
                    "name": "Payments",
                    "status": "Due",
                    "value": header_with_tooltip(
                        intcomma(stats.payments_due), "Worker payments earned but yet unpaid"
                    ),
                },
            ],
        },
    ]

    return render(request, "opportunity/opportunity_delivery_stat.html", {"opp_stats": opp_stats})


@require_POST
def exchange_rate_preview(request, org_slug, opp_id):
    opp = get_opportunity_or_404(opp_id, org_slug)

    rate_date = request.POST.get("date")
    usd_currency = request.POST.get("usd_currency", False) == "true"
    replace_amount = request.POST.get("should_replace_amount", False) == "true"  # condition when user toggles
    amount = None

    rate_date = datetime.datetime.strptime(rate_date, "%Y-%m-%d").date()
    try:
        amount = Decimal(request.POST.get("amount") or 0)
    except InvalidOperation:
        amount = Decimal(0)

    converted_amount = amount

    if not rate_date:
        exchange_info = "Please select a date for exchange rate."
        converted_amount_display = ""
    else:
        exchange_rate = ExchangeRate.latest_exchange_rate(opp.currency, rate_date)
        if exchange_rate:
            exchange_info = format_html(
                "Exchange Rate on {}: <b>{}</b>",
                rate_date.strftime("%d-%m-%Y"),
                exchange_rate.rate,
            )
            other_currency_amount = None
            currency = opp.currency

            if usd_currency:
                if replace_amount:
                    converted_amount = amount / exchange_rate.rate
                other_currency_amount = converted_amount * exchange_rate.rate
            else:
                if replace_amount:
                    converted_amount = amount * exchange_rate.rate
                other_currency_amount = converted_amount / exchange_rate.rate
                currency = "USD"

            converted_amount = round(converted_amount, 2)
            other_currency_amount = round(other_currency_amount, 2)

            converted_amount_display = format_html("Amount in {}: <b>{}</b>", currency, other_currency_amount)
        else:
            exchange_info = "Exchange rate not available for selected date."
            converted_amount_display = ""

    html = format_html(
        """
            <div id="exchange-rate-display" data-converted-amount="{converted_amount}">{exchange_info}</div>
            <div id="converted-amount">{converted_amount_display}</div>
        """,
        exchange_info=exchange_info,
        converted_amount_display=converted_amount_display,
        converted_amount=converted_amount,
    )
    return HttpResponse(html)


@login_required
@require_POST
def add_api_key(request, org_slug):
    form = HQApiKeyCreateForm(data=request.POST, auto_id="api_key_form_id_for_%s")

    if form.is_valid():
        api_key = form.save(commit=False)
        api_key.user = request.user
        api_key.save()
        form = HQApiKeyCreateForm(auto_id="api_key_form_id_for_%s")
    return HttpResponse(render_crispy_form(form))
