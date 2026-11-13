"""Shared views for the opportunity app; features are moving out into submodules (``invoices``, ``worker``).

Never import a submodule here: they import from this package, so it would be circular.
"""

import datetime
import json
import logging
from datetime import timedelta
from decimal import Decimal
from functools import cached_property
from http import HTTPStatus
from urllib.parse import urlencode, urlparse, urlunsplit

import httpx
import pghistory
from celery.result import AsyncResult
from crispy_forms.utils import render_crispy_form
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.humanize.templatetags.humanize import intcomma
from django.contrib.messages.views import SuccessMessageMixin
from django.core.cache import cache
from django.core.files.storage import default_storage, storages
from django.db.models import Count, F, FloatField, Func, Q, Sum, Value
from django.db.models.functions import Cast, Coalesce
from django.forms import modelformset_factory
from django.http import FileResponse, Http404, HttpResponse, HttpResponseBadRequest, HttpResponseNotFound, JsonResponse
from django.middleware.csrf import get_token
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.cache import patch_vary_headers
from django.utils.decorators import method_decorator
from django.utils.html import escape, format_html
from django.utils.safestring import mark_safe
from django.utils.text import slugify
from django.utils.timezone import is_aware, localtime, now
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from django.views.generic import CreateView, DetailView, TemplateView, UpdateView
from django_tables2 import RequestConfig, SingleTableView
from geopy import distance
from waffle import flag_is_active, switch_is_active

from commcare_connect.commcarehq.models import HQServer
from commcare_connect.connect_id_client import fetch_users
from commcare_connect.flags.flag_names import MICROPLANNING, WEEKLY_PERFORMANCE_REPORT
from commcare_connect.flags.switch_names import WORKER_VISITS_TASKS
from commcare_connect.form_receiver.serializers import XFormSerializer
from commcare_connect.microplanning.models import WorkAreaInaccessibilityRequest
from commcare_connect.opportunity.api.serializers.mobile import remove_opportunity_access_cache
from commcare_connect.opportunity.app_xml import AppNoBuildException
from commcare_connect.opportunity.decorators import require_manual_visit_verification
from commcare_connect.opportunity.exceptions import ListTooLongError, TaskAlreadyAssignedError
from commcare_connect.opportunity.filters import (
    AssignedTaskFilterSet,
    FilterMixin,
    OpportunityListFilterSet,
)
from commcare_connect.opportunity.forms import (
    AddBudgetExistingUsersForm,
    AddBudgetNewUsersForm,
    AddTaskTypeForm,
    AudioAttachmentTranscribeForm,
    CreateTaskForm,
    DeliverUnitFlagsForm,
    EditAssignedTaskForm,
    EditTaskTypeForm,
    FormJsonValidationRulesForm,
    HQApiKeyCreateForm,
    OpportunityChangeForm,
    OpportunityFinalizeForm,
    OpportunityInitForm,
    OpportunityInitUpdateForm,
    OpportunityUserInviteForm,
    OpportunityVerificationFlagsConfigForm,
    PaymentExportForm,
    PaymentUnitForm,
    SendMessageMobileUsersForm,
    VisitExportForm,
)
from commcare_connect.opportunity.helpers import (
    OpportunityData,
    amount_with_currency,
    api_key_owner_has_left,
    get_opportunity_delivery_progress,
    get_opportunity_funnel_progress,
    get_payment_report_data,
)
from commcare_connect.opportunity.models import (
    AssignedTask,
    AssignedTaskStatus,
    AudioAttachment,
    BlobMeta,
    CompletedWork,
    DeliverUnit,
    DeliverUnitFlagRules,
    FormJsonValidationRules,
    LearnModule,
    Opportunity,
    OpportunityAccess,
    OpportunityActiveEvent,
    OpportunityClaim,
    OpportunityClaimLimit,
    OpportunityStatus,
    OpportunityVerificationFlags,
    Payment,
    PaymentUnit,
    TaskType,
    UserInvite,
    UserInviteStatus,
    UserVisit,
    VisitReviewStatus,
    VisitValidationStatus,
)
from commcare_connect.opportunity.tables import (
    AssignedTaskListTable,
    CompletedWorkTable,
    DeliverUnitTable,
    LearnModuleTable,
    OpportunityTable,
    PaymentReportTable,
    PaymentUnitTable,
    ProgramManagerOpportunityTable,
    SuspendedUsersTable,
    TaskTable,
    header_with_tooltip,
)
from commcare_connect.opportunity.tasks import (
    add_connect_users,
    bulk_update_payments_task,
    bulk_update_visit_status_task,
    create_learn_modules_and_deliver_units,
    generate_deliver_status_export,
    generate_payment_export,
    generate_review_visit_export,
    generate_user_status_export,
    generate_visit_export,
    generate_work_status_export,
    get_payment_upload_key,
    invite_user,
    send_push_notification_task,
    update_user_and_send_invite,
)
from commcare_connect.opportunity.visit_import import (
    PAYMENT_IMPORT_FORMATS,
    ImportException,
    bulk_update_completed_work_status,
    bulk_update_visit_review_status,
    update_payment_accrued,
)
from commcare_connect.opportunity.worker_progress import (
    get_delivery_allocation,
    get_payment_unit_breakdown,
    get_worker_payments,
)
from commcare_connect.organization.decorators import (
    opp_admin_access_required,
    opp_standard_access_required,
    opp_view_access_required,
    opportunity_pm_required,
    opportunity_required,
    org_view_access_required,
    program_admin_access_required,
)
from commcare_connect.program.utils import (
    AccessLevel,
    is_opportunity_pm,
    opportunity_access_level_from_request,
    opportunity_by_id,
)
from commcare_connect.users.models import User
from commcare_connect.utils.analytics import GA_CUSTOM_DIMENSIONS, Event, GATrackingInfo, send_event_to_ga
from commcare_connect.utils.celery import (
    download_export_file,
    get_task_progress,
    render_export_status,
)
from commcare_connect.utils.commcarehq_api import CommCareHQAPIException
from commcare_connect.utils.datetime import get_start_end_date_range_with_time
from commcare_connect.utils.db import get_object_by_uuid_or_int
from commcare_connect.utils.file import get_file_extension
from commcare_connect.utils.flags import FlagLabels, Flags
from commcare_connect.utils.oauth_tokens import TokenRefreshError
from commcare_connect.utils.ocs_api import OcsApiError, list_chatbots, user_has_connected_ocs
from commcare_connect.utils.tables import (
    DATE_TIME_FORMAT,
    DEFAULT_PAGE_SIZE,
    PAGE_SIZE_OPTIONS,
    get_validated_page_size,
)

logger = logging.getLogger(__name__)

EXPORT_ROW_LIMIT = 10_000
_NEXT_WORKER_TASKS = "worker_tasks"


# Task id of the payment import whose outcome has already been shown to the user.
PAYMENT_IMPORT_CLAIMED_SESSION_KEY = "shown_payment_import"


def get_opportunity_or_404(opp_id):
    opportunity = opportunity_by_id(opp_id)

    if not opportunity:
        raise Http404(_("Opportunity not found."))
    return opportunity


class OpportunityObjectMixin:
    def get_opportunity_queryset(self):
        return Opportunity.objects.all()

    def get_opportunity(self):
        if cached := getattr(self.request, "opportunity", None):
            return cached

        opportunity = get_object_by_uuid_or_int(
            self.get_opportunity_queryset(),
            str(self.kwargs.get("opp_id")),
            uuid_field="opportunity_id",
        )
        self.request.opportunity = opportunity
        return opportunity

    def get_object(self, queryset=None):
        return self.get_opportunity()


class OrgContextSingleTableView(SingleTableView):
    def get_table_kwargs(self):
        kwargs = super().get_table_kwargs()
        kwargs["org_slug"] = self.request.org.slug
        return kwargs


@method_decorator(org_view_access_required, name="dispatch")
class OpportunityList(FilterMixin, SingleTableView):
    model = Opportunity
    table_class = ProgramManagerOpportunityTable
    template_name = "opportunity/opportunities_list.html"
    paginate_by = 15
    filter_class = OpportunityListFilterSet
    default_filters = {"status": OpportunityStatus.ACTIVE.value, "is_test": False}

    @cached_property
    def can_act_as_program_manager(self):
        org = self.request.org
        return org.program_manager or org.funder or org.watched_programs.exists()

    def get_template_names(self):
        # A history restore re-requests a searched URL over htmx but needs the whole page back.
        if self.request.htmx and not self.request.htmx.history_restore_request:
            return ["opportunity/partials/opportunities_table.html"]
        return super().get_template_names()

    def render_to_response(self, context, **response_kwargs):
        response = super().render_to_response(context, **response_kwargs)
        # The same URL serves the full page or just the table, so caches must not mix them up.
        patch_vary_headers(response, ("HX-Request", "HX-History-Restore-Request"))
        return response

    def get_context_data(self, *args, **kwargs):
        context = super().get_context_data(*args, **kwargs)
        context.update(self.get_filter_context())
        context["search_term"] = self.search_term
        context["search_carried_params"] = self._search_carried_params()
        return context

    @cached_property
    def search_term(self):
        return self.request.GET.get("q", "").strip()

    def _search_carried_params(self):
        """Query params a new search keeps: everything but the search itself and the page, which resets."""
        return [
            (key, value) for key, values in self.request.GET.lists() if key not in ("q", "page") for value in values
        ]

    def get_table_class(self):
        if self.can_act_as_program_manager:
            return ProgramManagerOpportunityTable
        return OpportunityTable

    def get_paginate_by(self, table):
        return get_validated_page_size(self.request)

    def get_table_kwargs(self):
        kwargs = super().get_table_kwargs()
        kwargs["org_slug"] = self.request.org.slug
        return kwargs

    def get_table_data(self):
        data = OpportunityData(
            self.request.org, self.can_act_as_program_manager, self.get_filter_values(), self.search_term
        )
        return data.get_data()


@method_decorator(program_admin_access_required, name="dispatch")
class OpportunityInit(CreateView):
    template_name = "opportunity/opportunity_init.html"
    form_class = OpportunityInitForm

    def get_success_url(self):
        return reverse("opportunity:add_payment_units", args=(self.request.org.slug, self.object.opportunity_id))

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        kwargs["org_slug"] = self.request.org.slug
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["api_key_form"] = HQApiKeyCreateForm(auto_id="api_key_form_id_for_%s")
        context["opportunity_created"] = False
        return context

    def form_valid(self, form: OpportunityInitForm):
        response = super().form_valid(form)
        create_learn_modules_and_deliver_units(self.object.id)
        return response


@method_decorator(program_admin_access_required, name="dispatch")
class OpportunityInitUpdate(OpportunityObjectMixin, UpdateView):
    model = Opportunity
    template_name = "opportunity/opportunity_init.html"
    form_class = OpportunityInitUpdateForm
    context_object_name = "opportunity"

    def get_success_url(self):
        return reverse("opportunity:add_payment_units", args=(self.request.org.slug, self.object.opportunity_id))

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        opportunity = getattr(self, "object", None)
        if opportunity is None:
            opportunity = self.get_object()
            self.object = opportunity
        kwargs["user"] = self.request.user
        kwargs["org_slug"] = self.request.org.slug
        kwargs["program"] = opportunity.program
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["api_key_form"] = HQApiKeyCreateForm(auto_id="api_key_form_id_for_%s")
        context["opportunity_created"] = True
        return context


@method_decorator(opportunity_pm_required, name="dispatch")
class OpportunityEdit(OpportunityObjectMixin, UpdateView):
    model = Opportunity
    template_name = "opportunity/opportunity_edit.html"
    form_class = OpportunityChangeForm

    @cached_property
    def active_history_events(self):
        return (
            OpportunityActiveEvent.objects.filter(pgh_obj=self.get_object())
            .select_related("pgh_context")
            .order_by("-pgh_created_at")
        )

    def get_success_url(self):
        return reverse("opportunity:detail", args=(self.request.org.slug, self.object.opportunity_id))

    def form_valid(self, form):
        opportunity = form.instance
        opportunity.modified_by = self.request.user.email
        end_date = form.cleaned_data["end_date"]
        if end_date:
            opportunity.end_date = end_date
        response = super().form_valid(form)
        users = form.cleaned_data["users"]
        if users:
            add_connect_users.delay(users, form.instance.id)

        return response

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if "api_key" in context["form"].fields:
            context.update(api_key_replacement_context(self.object))
        context["active_events"] = self.active_history_events
        return context

    def get_form_kwargs(self, *args, **kwargs):
        form_kwargs = super().get_form_kwargs(*args, **kwargs)
        form_kwargs["request"] = self.request
        if self.active_history_events:
            form_kwargs.update({"latest_active_history_event": self.active_history_events[0]})
        return form_kwargs


def api_key_replacement_context(opportunity):
    """Context for the add-key modal and the departed-owner warning on pages that edit an opportunity's key."""
    return {
        # The key select only lists keys on the opportunity's server, so a key for another one would vanish.
        "api_key_form": HQApiKeyCreateForm(auto_id="api_key_form_id_for_%s", fixed_hq_server=opportunity.hq_server),
        "api_key_owner_has_left": api_key_owner_has_left(opportunity),
    }


@method_decorator(opportunity_pm_required, name="dispatch")
class OpportunityFinalize(OpportunityObjectMixin, UpdateView):
    model = Opportunity
    template_name = "opportunity/opportunity_finalize.html"
    form_class = OpportunityFinalizeForm

    # Guarding get/post rather than dispatch keeps this behind the PM gate, which lives on the
    # mixin's dispatch and would be skipped by an override that returns before calling super().
    def get(self, request, *args, **kwargs):
        return self._redirect_if_no_payment_units(request) or super().get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        return self._redirect_if_no_payment_units(request) or super().post(request, *args, **kwargs)

    def _redirect_if_no_payment_units(self, request):
        self.object = self.get_object()
        if self.object.paymentunit_set.exists():
            return None
        messages.warning(request, "Please configure payment units before setting budget")
        return redirect("opportunity:add_payment_units", org_slug=request.org.slug, opp_id=self.object.opportunity_id)

    def get_success_url(self):
        return reverse("opportunity:detail", args=(self.request.org.slug, self.object.opportunity_id))

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        opportunity = self.object
        kwargs["current_start_date"] = opportunity.start_date
        kwargs["opportunity"] = opportunity
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

        response = super().form_valid(form)
        return response


@method_decorator(opp_view_access_required, name="dispatch")
class OpportunityDashboard(OpportunityObjectMixin, DetailView):
    model = Opportunity
    template_name = "opportunity/dashboard.html"

    def get(self, request, *args, **kwargs):
        self.object = self.get_object()
        if not self.object.is_setup_complete:
            messages.warning(request, "Please complete the opportunity setup to view it")
            return redirect(
                "opportunity:add_payment_units", org_slug=request.org.slug, opp_id=self.object.opportunity_id
            )
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

        def display_limit(value):
            return _("Unlimited") if value is None else str(value)

        context["path"] = [
            {"title": "Opportunities", "url": reverse("opportunity:list", kwargs={"org_slug": request.org.slug})},
            {
                "title": object.name,
                "url": reverse("opportunity:detail", args=(request.org.slug, object.opportunity_id)),
            },
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
                    display_limit(object.max_workers),
                    "Workers who have claimed, plus how many more the remaining budget can fund",
                ),
                "icon": "fa-users",
            },
            {
                "name": "Max Service Deliveries",
                "count": header_with_tooltip(
                    display_limit(object.allotted_visits),
                    "Maximum number of payment units that can be delivered. Each payment unit is a service delivery",
                ),
                "icon": "fa-gears",
            },
            {
                "name": "Max Budget",
                "count": header_with_tooltip(
                    amount_with_currency(object.total_budget, object.currency_code),
                    "Maximum payments that can be made for workers and organization",
                ),
                "icon": "fa-money-bill",
            },
        ]
        context["export_task_id"] = get_export_task_id(request, object)
        return context


@opp_standard_access_required
@opportunity_required
def export_user_visits(request, org_slug, opp_id):
    form = VisitExportForm(data=request.POST, opportunity=request.opportunity, org_slug=org_slug)
    if not form.is_valid():
        messages.error(request, form.errors)
        return redirect("opportunity:worker_list", request.org.slug, opp_id)

    export_format = form.cleaned_data["format"]
    from_date = form.cleaned_data["from_date"]
    to_date = form.cleaned_data["to_date"]
    status = form.cleaned_data["status"]
    flatten = form.cleaned_data["flatten_form_data"]
    result = generate_visit_export.delay(request.opportunity.pk, from_date, to_date, status, export_format, flatten)
    redirect_url = reverse("opportunity:worker_deliver", args=(request.org.slug, opp_id))
    return redirect(f"{redirect_url}?export_task_id={result.id}")


@opp_standard_access_required
@opportunity_required
@require_manual_visit_verification
def review_visit_export(request, org_slug, opp_id):
    form = VisitExportForm(data=request.POST, opportunity=request.opportunity, org_slug=org_slug, review_export=True)
    redirect_url = reverse("opportunity:worker_deliver", args=(org_slug, opp_id))
    if not form.is_valid():
        messages.error(request, form.errors)
        return redirect(redirect_url)

    export_format = form.cleaned_data["format"]
    from_date = form.cleaned_data["from_date"]
    to_date = form.cleaned_data["to_date"]
    status = form.cleaned_data["status"]

    result = generate_review_visit_export.delay(request.opportunity.pk, from_date, to_date, status, export_format)
    return redirect(f"{redirect_url}?export_task_id={result.id}")


def get_export_task_id(request, opportunity):
    if opportunity_access_level_from_request(request, opportunity) < AccessLevel.STANDARD:
        return None
    return request.GET.get("export_task_id")


@login_required
@require_GET
def export_status(request, org_slug, task_id):
    def ownership_check(request, task_meta):
        args = task_meta.get("args") or []
        if not args:
            raise Http404("Export not found.")
        opportunity = get_opportunity_or_404(args[0])
        if opportunity_access_level_from_request(request, opportunity) < AccessLevel.STANDARD:
            raise Http404()

    return render_export_status(
        request,
        task_id=task_id,
        download_url=reverse("opportunity:download_export", args=(org_slug, task_id)),
        export_status_url=reverse("opportunity:export_status", args=(org_slug, task_id)),
        ownership_check=ownership_check,
    )


@login_required
@require_GET
def download_export(request, org_slug, task_id):
    args = AsyncResult(task_id).args or []
    if not args:
        raise Http404("Export not found.")
    opportunity = get_opportunity_or_404(args[0])
    if opportunity_access_level_from_request(request, opportunity) < AccessLevel.STANDARD:
        raise Http404()
    op_slug = slugify(opportunity.name)
    return download_export_file(
        task_id=task_id,
        filename_without_ext=f"{org_slug}_{op_slug}_export",
    )


@opp_standard_access_required
@opportunity_required
@require_POST
@require_manual_visit_verification
def update_visit_status_import(request, org_slug=None, opp_id=None):
    file = request.FILES.get("visits")
    file_format = get_file_extension(file)
    redirect_url = reverse("opportunity:worker_deliver", args=(org_slug, opp_id))

    if file_format not in ("csv", "xlsx"):
        messages.error(request, f"Invalid file format. Only 'CSV' and 'XLSX' are supported. Got {file_format}")
    else:
        file_path = f"{request.opportunity.pk}_{datetime.datetime.now().isoformat}_visit_import"
        saved_path = default_storage.save(file_path, file)
        tracking_info = GATrackingInfo.from_request(request).dict()
        result = bulk_update_visit_status_task.delay(request.opportunity.pk, saved_path, file_format, tracking_info)
        redirect_url = f"{redirect_url}?export_task_id={result.id}"
    return redirect(redirect_url)


@opp_standard_access_required
@require_POST
@opportunity_required
@require_manual_visit_verification
def review_visit_import(request, org_slug=None, opp_id=None):
    file = request.FILES.get("visits")
    redirect_url = reverse("opportunity:worker_deliver", args=(org_slug, opp_id))
    try:
        status = bulk_update_visit_review_status(request.opportunity, file)
    except ImportException as e:
        messages.error(request, e.message)
    else:
        if status.missing_visits:
            messages.warning(request, mark_safe(status.get_missing_message()))
        if status.locked_visits:
            messages.warning(request, mark_safe(status.get_locked_message()))
        if status.seen_visits:
            messages.success(
                request, mark_safe(f"Visit review updated successfully for {len(status.seen_visits)} visits.")
            )
    return redirect(redirect_url)


@opp_standard_access_required
@opportunity_required
def add_budget_existing_users(request, org_slug=None, opp_id=None):
    opportunity_access = OpportunityAccess.objects.filter(opportunity=request.opportunity)
    opportunity_claims = OpportunityClaim.objects.filter(opportunity_access__in=opportunity_access).annotate(
        total_max_visits=Coalesce(Sum("opportunityclaimlimit__max_visits"), Value(0)),
        total_per_visit_cost=Coalesce(
            Sum(
                F("opportunityclaimlimit__payment_unit__amount") + F("opportunityclaimlimit__payment_unit__org_amount")
            ),
            Value(0),
        ),
    )

    form = AddBudgetExistingUsersForm(
        opportunity_claims=opportunity_claims,
        opportunity=request.opportunity,
        data=request.POST or None,
    )
    if form.is_valid():
        form.save()

        number_of_visits = form.cleaned_data.get("number_of_visits")
        selected_users = form.cleaned_data.get("selected_users")
        end_date = form.cleaned_data.get("end_date")
        adjustment_type = form.cleaned_data.get("adjustment_type")
        message_parts = []

        if number_of_visits and selected_users:
            visit_text = f"visits by {number_of_visits}"
            user_text = f"{len(selected_users)} worker{'s' if len(selected_users) != 1 else ''}"
            change_type = "Increased" if adjustment_type == form.AdjustmentType.INCREASE_VISITS else "Decreased"
            message_parts.append(f"{change_type} {visit_text} for {user_text}.")

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
    if request.is_opportunity_pm:
        tabs.append(
            {
                "key": "new_workers",
                "label": "New Connect Workers",
            }
        )

    path = [
        {"title": "Opportunities", "url": reverse("opportunity:list", args=(request.org.slug,))},
        {
            "title": request.opportunity.name,
            "url": reverse("opportunity:detail", args=(request.org.slug, request.opportunity.opportunity_id)),
        },
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
            "per_visit_costs_json": json.dumps({claim.id: claim.total_per_visit_cost for claim in opportunity_claims}),
            "opportunity": request.opportunity,
        },
    )


@opp_standard_access_required
@opportunity_required
def add_budget_new_users(request, org_slug=None, opp_id=None):
    program_manager = is_opportunity_pm(request, request.opportunity)

    form = AddBudgetNewUsersForm(
        opportunity=request.opportunity,
        program_manager=program_manager,
        data=request.POST or None,
    )

    if form.is_valid():
        form.save()
        budget_increase = form.budget_increase
        direction = "added to" if budget_increase >= 0 else "removed from"
        messages.success(
            request,
            f"{request.opportunity.currency_code} {abs(form.budget_increase)} was {direction} the opportunity budget.",
        )

        redirect_url = reverse("opportunity:add_budget_existing_users", args=[org_slug, opp_id])
        redirect_url += "?active_tab=new_users"
        response = HttpResponse()
        response["HX-Redirect"] = redirect_url
        return response

    csrf_token = get_token(request)
    form_html = f"""
        <form id="form-content"
              hx-post="{reverse("opportunity:add_budget_new_users", args=[org_slug, opp_id])}"
              hx-trigger="submit"
              hx-headers='{{"X-CSRFToken": "{csrf_token}"}}'>
            <input type="hidden" name="csrfmiddlewaretoken" value="{csrf_token}">
            {render_crispy_form(form)}
        </form>
        """

    return HttpResponse(mark_safe(form_html))


@opp_standard_access_required
@opportunity_required
def export_users_for_payment(request, org_slug, opp_id):
    form = PaymentExportForm(data=request.POST)
    if not form.is_valid():
        messages.error(request, form.errors)
        return redirect("opportunity:worker_payments", org_slug, opp_id)

    export_format = form.cleaned_data["format"]
    result = generate_payment_export.delay(request.opportunity.pk, export_format)
    redirect_url = reverse("opportunity:worker_payments", args=(request.org.slug, opp_id))
    return redirect(f"{redirect_url}?export_task_id={result.id}")


@opp_standard_access_required
@opportunity_required
@require_POST
def payment_import(request, org_slug=None, opp_id=None):
    file = request.FILES.get("payments")
    redirect_url = reverse("opportunity:worker_payments", args=(org_slug, opp_id))
    redirect_to_tab = f"{redirect_url}?{request.GET.copy().urlencode()}"

    file_format = get_file_extension(file)
    if file_format not in PAYMENT_IMPORT_FORMATS:
        supported_file_formats = ", ".join(file_format.upper() for file_format in PAYMENT_IMPORT_FORMATS)
        messages.error(
            request,
            _("File format not supported. Please upload a %(supported)s file.")
            % {"supported": supported_file_formats},
        )
        return redirect(redirect_to_tab)

    lock = cache.lock(get_payment_upload_key(request.opportunity.pk))

    if lock.locked():
        messages.error(request, _("Another payment import is in progress. Please try again later."))
        return redirect(redirect_to_tab)

    file_path = f"{request.opportunity.pk}_{datetime.datetime.now().isoformat}_payment_import"
    saved_path = default_storage.save(file_path, file)

    result = bulk_update_payments_task.delay(request.opportunity.pk, saved_path, file_format)
    return redirect(f"{redirect_url}?payment_import_task_id={result.id}")


@login_required
@require_GET
def render_payment_import_progress(request, org_slug, task_id):
    """Renders the payment import modal: a spinner while the import runs, then the row errors
    that stopped it. An import that finishes without row errors refreshes the page instead, so
    its outcome shows up as a standard banner."""

    def ownership_check(request, task_meta):
        args = task_meta.get("args") or []
        if not args:
            raise Http404("Import not found.")
        opportunity = get_opportunity_or_404(task_meta.get("args")[0])
        if opportunity_access_level_from_request(request, opportunity) < AccessLevel.STANDARD:
            raise Http404()

    progress = get_task_progress(request, task_id, ownership_check)
    finished = progress["complete"] or progress.get("error")
    if finished and not progress["errors"]:
        response = HttpResponse()
        response["HX-Refresh"] = "true"
        return response
    if finished:
        claim_task_outcome(request, PAYMENT_IMPORT_CLAIMED_SESSION_KEY, task_id)

    context = {
        "finished": finished,
        "progress": progress,
        "records_label": _("Payments"),
        "status_url": reverse("opportunity:payment_import_status", args=(org_slug, task_id)),
    }
    return render(request, "opportunity/payment_import_modal.html", context)


def claim_task_outcome(request, session_key, task_id):
    """Whether this request should show the task's outcome, claiming it if so.

    A task reports itself from the task id left in the URL, so a refresh or a back navigation
    would otherwise show the same banner, modal or notification again. The first request to ask
    for an outcome claims it; later ones are told there is nothing left to show.
    """
    if request.session.get(session_key) == task_id:
        return False
    request.session[session_key] = task_id
    return True


@opportunity_pm_required
@opportunity_required
def add_payment_units(request, org_slug=None, opp_id=None):
    if request.POST:
        return add_payment_unit(request, org_slug=org_slug, opp_id=opp_id)
    opportunity = request.opportunity
    paymentunit_count = PaymentUnit.objects.filter(opportunity=opportunity).count()
    return render(
        request,
        "opportunity/add_payment_units.html",
        dict(opportunity=opportunity, paymentunit_count=paymentunit_count),
    )


@opportunity_pm_required
@opportunity_required
def add_payment_unit(request, org_slug=None, opp_id=None):
    deliver_units = DeliverUnit.objects.filter(
        Q(payment_unit__isnull=True) | Q(payment_unit__opportunity__active=False), app=request.opportunity.deliver_app
    )
    form = PaymentUnitForm(
        deliver_units=deliver_units,
        data=request.POST or None,
        payment_units=request.opportunity.paymentunit_set.filter(parent_payment_unit__isnull=True).all(),
        org_slug=org_slug,
        opportunity=request.opportunity,
    )
    if form.is_valid():
        form.instance.opportunity = request.opportunity
        form.save()
        link_deliver_units(
            form.instance, form.cleaned_data["required_deliver_units"], form.cleaned_data["optional_deliver_units"]
        )
        sub_payment_units = form.cleaned_data["payment_units"]
        PaymentUnit.objects.filter(id__in=sub_payment_units, parent_payment_unit__isnull=True).update(
            parent_payment_unit=form.instance.id
        )
        messages.success(request, _("Payment unit %(name)s created.") % {"name": form.instance.name})
        claims = OpportunityClaim.objects.filter(opportunity_access__opportunity=request.opportunity)
        for claim in claims:
            OpportunityClaimLimit.create_claim_limits(request.opportunity, claim)
        return redirect(
            "opportunity:add_payment_units", org_slug=request.org.slug, opp_id=request.opportunity.opportunity_id
        )
    elif request.POST:
        return render(
            request,
            "opportunity/add_payment_units.html",
            dict(
                opportunity=request.opportunity,
                paymentunit_count=PaymentUnit.objects.filter(opportunity=request.opportunity).count(),
                form=form,
                form_title=_("Add Payment Unit"),
            ),
        )

    path = [
        {"title": _("Opportunities"), "url": reverse("opportunity:list", args=(request.org.slug,))},
        {
            "title": request.opportunity.name,
            "url": reverse("opportunity:detail", args=(request.org.slug, request.opportunity.opportunity_id)),
        },
        {
            "title": _("Payment unit"),
        },
    ]
    return render(
        request,
        "components/partial_form.html" if request.GET.get("partial") == "True" else "components/form.html",
        dict(
            title=f"{request.org.slug} - {request.opportunity.name}",
            form_title=_("Add Payment Unit"),
            form=form,
            path=path,
        ),
    )


@opportunity_pm_required
@opportunity_required
def edit_payment_unit(request, org_slug=None, opp_id=None, pk=None):
    payment_unit = get_object_or_404(PaymentUnit, payment_unit_id=pk, opportunity=request.opportunity)
    deliver_units = DeliverUnit.objects.filter(
        Q(payment_unit__isnull=True) | Q(payment_unit=payment_unit) | Q(payment_unit__opportunity__active=False),
        app=request.opportunity.deliver_app,
    )
    exclude_payment_units = [payment_unit.pk]
    if payment_unit.parent_payment_unit_id:
        exclude_payment_units.append(payment_unit.parent_payment_unit_id)
    payment_unit_deliver_units = {deliver_unit.pk for deliver_unit in payment_unit.deliver_units.all()}
    opportunity_payment_units = (
        request.opportunity.paymentunit_set.filter(
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
        opportunity=request.opportunity,
    )
    if form.is_valid():
        form.save()
        required_deliver_units = form.cleaned_data["required_deliver_units"]
        optional_deliver_units = form.cleaned_data["optional_deliver_units"]
        link_deliver_units(form.instance, required_deliver_units, optional_deliver_units)
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
        if request.is_opportunity_pm:
            return redirect(
                "opportunity:finalize", org_slug=request.org.slug, opp_id=request.opportunity.opportunity_id
            )
        else:
            return redirect("opportunity:detail", org_slug=request.org.slug, opp_id=request.opportunity.opportunity_id)

    path = [
        {"title": "Opportunities", "url": reverse("opportunity:list", args=(request.org.slug,))},
        {
            "title": request.opportunity.name,
            "url": reverse("opportunity:detail", args=(request.org.slug, request.opportunity.opportunity_id)),
        },
        {
            "title": "Payment unit",
        },
    ]
    return render(
        request,
        "components/form.html",
        dict(
            title=f"{request.org.slug} - {request.opportunity.name}",
            form_title=_("Edit Payment Unit"),
            form=form,
            path=path,
        ),
    )


def link_deliver_units(payment_unit, required_deliver_units, optional_deliver_units):
    # The form only offers unattached units and units from inactive opportunities, so this may move
    # a unit off an inactive opportunity's payment unit
    DeliverUnit.objects.filter(id__in=required_deliver_units).update(payment_unit=payment_unit, optional=False)
    DeliverUnit.objects.filter(id__in=optional_deliver_units).update(payment_unit=payment_unit, optional=True)


@opp_standard_access_required
@opportunity_required
def export_user_status(request, org_slug, opp_id):
    form = PaymentExportForm(data=request.POST)
    if not form.is_valid():
        messages.error(request, form.errors)
        return redirect("opportunity:worker_list", request.org.slug, opp_id)

    export_format = form.cleaned_data["format"]
    result = generate_user_status_export.delay(request.opportunity.pk, export_format)
    redirect_url = reverse("opportunity:worker_list", args=(request.org.slug, opp_id))
    return redirect(f"{redirect_url}?export_task_id={result.id}")


@opp_standard_access_required
@opportunity_required
def export_deliver_status(request, org_slug, opp_id):
    form = PaymentExportForm(data=request.POST)
    if not form.is_valid():
        messages.error(request, form.errors)
        return redirect("opportunity:detail", request.org.slug, opp_id)

    export_format = form.cleaned_data["format"]
    result = generate_deliver_status_export.delay(request.opportunity.pk, export_format)
    redirect_url = reverse("opportunity:detail", args=(request.org.slug, opp_id))
    return redirect(f"{redirect_url}?export_task_id={result.id}")


@opp_standard_access_required
@opportunity_required
@require_POST
def payment_delete(request, org_slug=None, opp_id=None, access_id=None, pk=None):
    opportunity_access = get_object_or_404(
        OpportunityAccess, opportunity_access_id=access_id, opportunity=request.opportunity
    )
    payment = get_object_or_404(Payment, opportunity_access=opportunity_access, payment_id=pk)
    payment_id = payment.id
    payment_uuid = payment.payment_id
    payment.delete()

    send_push_notification_task.delay(
        [opportunity_access.user_id],
        _("Payment updated"),
        _("There has been an adjustment to your earnings for {}.").format(opportunity_access.opportunity.name),
        extra_data={
            "opportunity_status": "delivery",
            "action": "ccc_generic_opportunity",
            "key": "payment_rollback",
            "opportunity_id": str(request.opportunity.id),
            "payment_id": str(payment_id),
            "opportunity_uuid": str(request.opportunity.opportunity_id),
            "payment_uuid": str(payment_uuid),
        },
    )
    return redirect("opportunity:worker_payments", org_slug, opp_id)


@opp_admin_access_required
@opportunity_required
def send_message_mobile_users(request, org_slug=None, opp_id=None):
    user_ids = OpportunityAccess.objects.filter(opportunity=request.opportunity, accepted=True).values_list(
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
        {
            "title": request.opportunity.name,
            "url": reverse("opportunity:detail", args=(org_slug, request.opportunity.opportunity_id)),
        },
        {"title": "Send Message", "url": request.path},
    ]
    return render(
        request,
        "opportunity/send_message.html",
        context=dict(
            title=f"{request.org.slug} - {request.opportunity.name}",
            form_title="Send Message",
            form=form,
            users=users,
            user_ids=list(user_ids),
            path=path,
        ),
    )


@opp_standard_access_required
@require_POST
@opportunity_required
@require_manual_visit_verification
def approve_visits(request, org_slug, opp_id):
    visit_ids = request.POST.getlist("visit_ids[]")

    visits = (
        UserVisit.objects.filter(id__in=visit_ids, opportunity=request.opportunity)
        .filter(~Q(status=VisitValidationStatus.approved) | Q(review_status=VisitReviewStatus.disagree))
        .prefetch_related("opportunity")
        .select_related("work_area", "work_area__opportunity_access")
        .only("status", "review_status", "flagged", "justification", "review_created_on", "work_area")
    )

    if len(visits) > max(PAGE_SIZE_OPTIONS):
        return HttpResponseBadRequest(
            "Maximum 100 visits allowed for bulk approval",
            headers={"HX-Trigger": "form_error"},
        )

    work_areas_to_update = []
    today = now()
    for visit in visits:
        visit.status = VisitValidationStatus.approved
        visit.review_created_on = today
        if visit.review_status == VisitReviewStatus.disagree:
            visit.review_status = VisitReviewStatus.pending
            visit.review_status_modified_date = today
        if visit.flagged:
            justification = request.POST.get("justification")
            if not justification:
                return HttpResponse(
                    "Justification is mandatory for flagged visits.",
                    status=400,
                    headers={"HX-Trigger": "form_error"},
                )
            visit.justification = justification
        if visit.work_area:
            work_areas_to_update.append(visit.work_area)

    user_ids = list(visits.values_list("user_id", flat=True).distinct())
    approved_count = UserVisit.objects.bulk_update(
        visits,
        [
            "status",
            "status_modified_date",
            "review_created_on",
            "review_status",
            "review_status_modified_date",
            "justification",
        ],
    )
    if user_ids:
        update_payment_accrued(opportunity=request.opportunity, users=user_ids, incremental=True)
    send_event_to_ga(request, Event("bulk_approve_confirm", {"updated": approved_count, "total": len(visit_ids)}))

    for work_area in work_areas_to_update:
        work_area.update_status()

    return HttpResponse(status=200, headers={"HX-Trigger": "reload_table"})


@opp_standard_access_required
@opportunity_required
@require_POST
@require_manual_visit_verification
def reject_visits(request, org_slug=None, opp_id=None):
    visit_ids = request.POST.getlist("visit_ids[]")
    reason = request.POST.get("reason", "").strip()

    visits = UserVisit.objects.filter(id__in=visit_ids, opportunity=request.opportunity)
    if len(visits) > max(PAGE_SIZE_OPTIONS):
        return HttpResponseBadRequest(
            "Maximum 100 visits allowed for bulk rejection",
            headers={"HX-Trigger": "form_error"},
        )

    updated_count = visits.exclude(
        Q(status=VisitValidationStatus.rejected) | Q(review_status=VisitReviewStatus.agree)
    ).update(status=VisitValidationStatus.rejected, reason=reason, status_modified_date=now())
    if visits.exists():
        user_ids = visits.values_list("user_id", flat=True).distinct()
        update_payment_accrued(opportunity=request.opportunity, users=user_ids)

    send_event_to_ga(request, Event("bulk_reject_confirm", {"updated": updated_count, "total": len(visit_ids)}))

    return HttpResponse(status=200, headers={"HX-Trigger": "reload_table"})


@opp_view_access_required
@opportunity_required
def fetch_attachment(request, org_slug, opp_id, blob_id):
    blob_meta = get_object_or_404(BlobMeta, blob_id=blob_id)

    if not (
        UserVisit.objects.filter(opportunity=request.opportunity, xform_id=blob_meta.parent_id).exists()
        or WorkAreaInaccessibilityRequest.objects.filter(
            work_area__opportunity=request.opportunity, xform_id=blob_meta.parent_id
        ).exists()
    ):
        return HttpResponseNotFound()

    try:
        attachment = storages["default"].open(blob_id)
    except FileNotFoundError:
        return HttpResponseNotFound()
    return FileResponse(attachment, filename=blob_meta.name, content_type=blob_meta.content_type)


@opp_view_access_required
@opportunity_required
def fetch_audio_attachment(request, org_slug, opp_id, pk):
    audio = get_object_or_404(AudioAttachment, pk=pk, user_visit__opportunity=request.opportunity)

    try:
        attachment = storages["default"].open(audio.blob_id)
    except FileNotFoundError:
        return HttpResponseNotFound()
    return FileResponse(attachment, filename=audio.name, content_type=audio.content_type)


@method_decorator(opp_standard_access_required, name="dispatch")
class AudioAttachmentTranscribe(SuccessMessageMixin, OpportunityObjectMixin, UpdateView):
    model = AudioAttachment
    form_class = AudioAttachmentTranscribeForm
    template_name = "opportunity/audio_attachment_transcribe.html"
    success_message = gettext_lazy("Transcript saved.")

    def dispatch(self, request, *args, **kwargs):
        if request.is_opportunity_pm:
            raise Http404()
        return super().dispatch(request, *args, **kwargs)

    def get_object(self, queryset=None):
        return get_object_or_404(
            AudioAttachment.objects.select_related("user_visit__user", "user_visit__opportunity__program"),
            pk=self.kwargs["pk"],
            user_visit__opportunity=self.get_opportunity(),
        )

    def get_visit_details_url(self):
        return (
            f"{reverse('opportunity:user_visits_list', args=(self.kwargs['org_slug'], self.kwargs['opp_id']))}"
            f"?{urlencode({'user': self.object.user_visit.user.user_id})}"
        )

    def get_breadcrumbs(self, org_slug, opp_id):
        user_visit = self.object.user_visit
        opportunity = user_visit.opportunity

        return [
            {"title": opportunity.program.name, "url": reverse("program:home", args=(org_slug,))},
            {"title": _("Opportunities"), "url": reverse("opportunity:list", args=(org_slug,))},
            {
                "title": opportunity.name,
                "url": reverse("opportunity:detail", args=(org_slug, opp_id)),
            },
            {
                "title": _("Connect Workers"),
                "url": reverse("opportunity:worker_deliver", args=(org_slug, opp_id)),
            },
            {"title": user_visit.user.name, "url": self.get_visit_details_url()},
            {"title": _("Transcribe Audio"), "url": self.request.path},
        ]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["path"] = self.get_breadcrumbs(self.kwargs["org_slug"], self.kwargs["opp_id"])
        context["visit_details_url"] = self.get_visit_details_url()
        return context

    def get_success_url(self):
        return self.get_visit_details_url()


@opportunity_pm_required
@opportunity_required
def verification_flags_config(request, org_slug=None, opp_id=None):
    verification_flags = OpportunityVerificationFlags.objects.filter(opportunity=request.opportunity).first()
    form = OpportunityVerificationFlagsConfigForm(
        instance=verification_flags, data=request.POST or None, opportunity=request.opportunity
    )
    deliver_unit_count = DeliverUnit.objects.filter(app=request.opportunity.deliver_app).count()
    DeliverUnitFlagsFormset = modelformset_factory(
        DeliverUnitFlagRules, DeliverUnitFlagsForm, extra=deliver_unit_count, max_num=deliver_unit_count
    )
    deliver_unit_flags = DeliverUnitFlagRules.objects.filter(opportunity=request.opportunity)
    deliver_unit_formset = DeliverUnitFlagsFormset(
        form_kwargs={"opportunity": request.opportunity},
        prefix="deliver_unit",
        queryset=deliver_unit_flags,
        data=request.POST or None,
        initial=[
            {"deliver_unit": du}
            for du in request.opportunity.deliver_app.deliver_units.exclude(
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
        form_kwargs={"opportunity": request.opportunity},
        prefix="form_json",
        queryset=FormJsonValidationRules.objects.filter(opportunity=request.opportunity),
        data=request.POST or None,
    )
    if (
        request.method == "POST"
        and form.is_valid()
        and deliver_unit_formset.is_valid()
        and form_json_formset.is_valid()
    ):
        verification_flags = form.save(commit=False)
        verification_flags.opportunity = request.opportunity
        verification_flags.save()
        for du_form in deliver_unit_formset.forms:
            if du_form.is_valid() and du_form.cleaned_data != {}:
                du_form.instance.opportunity = request.opportunity
                du_form.save()
        for fj_form in form_json_formset.forms:
            if fj_form.is_valid() and fj_form.cleaned_data != {}:
                fj_form.instance.opportunity = request.opportunity
                fj_form.save()
        messages.success(request, "Verification rules saved successfully.")

    path = [
        {"title": "Opportunities", "url": reverse("opportunity:list", args=(org_slug,))},
        {
            "title": request.opportunity.name,
            "url": reverse("opportunity:detail", args=(org_slug, request.opportunity.opportunity_id)),
        },
        {
            "title": _("Verification Rules Configuration"),
            "url": request.path,
        },
    ]
    return render(
        request,
        "opportunity/verification_flags_config.html",
        context=dict(
            opportunity=request.opportunity,
            title=f"{request.org.slug} - {request.opportunity.name}",
            form=form,
            deliver_unit_formset=deliver_unit_formset,
            form_json_formset=form_json_formset,
            path=path,
        ),
    )


@method_decorator(opportunity_pm_required, name="dispatch")
@method_decorator(opp_standard_access_required, name="dispatch")
class TaskTypesConfig(OpportunityObjectMixin, TemplateView):
    template_name = "opportunity/task_types_config.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        opportunity = self.get_opportunity()
        org_slug = self.request.org.slug

        tasks = TaskType.objects.filter(app=opportunity.deliver_app)
        path = [
            {"title": _("Opportunities"), "url": reverse("opportunity:list", args=(org_slug,))},
            {
                "title": opportunity.name,
                "url": reverse("opportunity:detail", args=(org_slug, opportunity.opportunity_id)),
            },
            {"title": _("Configure Task Types"), "url": self.request.path},
        ]
        table = TaskTable(tasks, org_slug=org_slug, opp_id=opportunity.opportunity_id)
        RequestConfig(self.request, paginate={"per_page": get_validated_page_size(self.request)}).configure(table)
        context.update(
            {
                "opportunity": opportunity,
                "table": table,
                "form": kwargs.get("form", AddTaskTypeForm(opportunity=opportunity, org_slug=org_slug)),
                "path": path,
            }
        )
        return context

    def get(self, request, org_slug, opp_id):
        return self.render_to_response(self.get_context_data())

    def post(self, request, org_slug, opp_id):
        opportunity = self.get_opportunity()
        form = AddTaskTypeForm(data=request.POST, opportunity=opportunity, org_slug=org_slug)
        if form.is_valid():
            form.save()
            messages.success(request, _("Task type added successfully."))
            return redirect("opportunity:task_types_config", org_slug=org_slug, opp_id=opp_id)
        return self.render_to_response(self.get_context_data(form=form))


def get_ocs_task_section_context(request):
    if not user_has_connected_ocs(request.user):
        return _ocs_connect_prompt_context(request)
    try:
        chatbots = list_chatbots(request.user)
    except TokenRefreshError:
        return _ocs_connect_prompt_context(request)
    except OcsApiError:
        return {"ocs_connected": True, "ocs_error": True}
    return {
        "ocs_connected": True,
        "chatbots": chatbots,
        "selected_chatbot_id": request.GET.get("selected"),
    }


def _ocs_connect_prompt_context(request):
    """
    Since the OCS connect prompt is loaded as an htmx request we need to make
    sure the ocs "next" url references the parent page the htmx request came
    from.
    """
    return {
        "ocs_connected": False,
        "ocs_next_url": _hx_current_path(request),
    }


def _hx_current_path(request):
    """
    Relative path (path + query) of the page the htmx request came from.
    """
    hx_current_url = request.headers.get("HX-Current-URL")
    if not hx_current_url:
        return request.get_full_path()
    parsed = urlparse(hx_current_url)
    return urlunsplit(("", "", parsed.path, parsed.query, ""))


@method_decorator(opportunity_pm_required, name="dispatch")
@method_decorator(opp_standard_access_required, name="dispatch")
class TaskTypeOcsSection(OpportunityObjectMixin, View):
    def get(self, request, org_slug, opp_id):
        context = get_ocs_task_section_context(request)
        context["ocs_section_url"] = request.get_full_path()
        return render(request, "opportunity/_ocs_task_section.html", context)


@method_decorator(opportunity_pm_required, name="dispatch")
@method_decorator(opp_standard_access_required, name="dispatch")
class EditTaskType(OpportunityObjectMixin, UpdateView):
    template_name = "opportunity/edit_task_type_form.html"
    form_class = EditTaskTypeForm
    model = TaskType

    def get_object(self, queryset=None):
        return get_object_or_404(self.model, pk=self.kwargs["pk"], app=self.get_opportunity().deliver_app)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["hx_post_url"] = reverse(
            "opportunity:edit_task_type",
            args=(self.kwargs["org_slug"], self.kwargs["opp_id"], self.kwargs["pk"]),
        )
        return context

    def form_valid(self, form):
        form.save()
        messages.success(self.request, _("Task type updated successfully."))
        response = HttpResponse()
        response["HX-Redirect"] = reverse(
            "opportunity:task_types_config", args=(self.kwargs["org_slug"], self.kwargs["opp_id"])
        )
        return response


@opp_standard_access_required
@csrf_exempt
@require_http_methods(["DELETE"])
@opportunity_required
def delete_form_json_rule(request, org_slug=None, opp_id=None, pk=None):
    if not request.is_opportunity_pm:
        return redirect("opportunity:detail", org_slug=org_slug, opp_id=opp_id)

    form_json_rule = get_object_or_404(
        FormJsonValidationRules,
        opportunity=request.opportunity,
        form_json_validation_rules_id=pk,
    )
    form_json_rule.delete()
    return HttpResponse(status=200)


@method_decorator(opp_view_access_required, name="dispatch")
class OpportunityCompletedWorkTable(OpportunityObjectMixin, SingleTableView):
    model = CompletedWork
    paginate_by = 25
    table_class = CompletedWorkTable
    template_name = "tables/single_table.html"

    def get_queryset(self):
        access_objects = OpportunityAccess.objects.filter(opportunity=self.get_opportunity())
        return list(
            filter(lambda cw: cw.completed, CompletedWork.objects.filter(opportunity_access__in=access_objects))
        )


@opp_standard_access_required
@opportunity_required
def export_completed_work(request, org_slug, opp_id):
    form = PaymentExportForm(data=request.POST)
    if not form.is_valid():
        messages.error(request, form.errors)
        return redirect("opportunity:detail", request.org.slug, opp_id)

    export_format = form.cleaned_data["format"]
    result = generate_work_status_export.delay(request.opportunity.pk, export_format)
    redirect_url = reverse("opportunity:detail", args=(request.org.slug, opp_id))
    return redirect(f"{redirect_url}?export_task_id={result.id}")


@opp_standard_access_required
@opportunity_required
@require_POST
def update_completed_work_status_import(request, org_slug=None, opp_id=None):
    file = request.FILES.get("visits")
    try:
        status = bulk_update_completed_work_status(request.opportunity, file)
    except ImportException as e:
        messages.error(request, e.message)
    else:
        message = f"Payment Verification status updated successfully for {len(status)} completed works."
        if status.missing_completed_works:
            message += status.get_missing_message()
        messages.success(request, mark_safe(message))
    return redirect("opportunity:detail", org_slug, opp_id)


@opportunity_pm_required
@opportunity_required
@require_POST
def suspend_user(request, org_slug=None, opp_id=None, pk=None):
    access = get_object_or_404(OpportunityAccess, opportunity=request.opportunity, opportunity_access_id=pk)
    access.suspended = True
    access.suspension_date = now()
    access.suspension_reason = request.POST.get("reason", "")
    access.save()

    # Clear the cached opportunity access for the suspended user
    remove_opportunity_access_cache(access.user, access.opportunity)

    url = reverse("opportunity:user_visits_list", args=(org_slug, opp_id))
    return redirect(f"{url}?user={access.user.user_id}")


@require_POST
@opportunity_pm_required
@opportunity_required
def revoke_user_suspension(request, org_slug=None, opp_id=None, pk=None):
    access = get_object_or_404(OpportunityAccess, opportunity=request.opportunity, opportunity_access_id=pk)
    access.suspended = False
    access.save()
    remove_opportunity_access_cache(access.user, access.opportunity)
    return HttpResponse(headers={"HX-Redirect": request.POST.get("next", "/")})


@opp_standard_access_required
@opportunity_required
def suspended_users_list(request, org_slug=None, opp_id=None):
    access_objects = OpportunityAccess.objects.filter(opportunity=request.opportunity, suspended=True)
    table = SuspendedUsersTable(access_objects, has_suspension_perm=request.is_opportunity_pm)
    RequestConfig(request, paginate={"per_page": get_validated_page_size(request)}).configure(table)
    path = []
    path.append({"title": "Programs", "url": reverse("program:home", args=(org_slug,))})
    path.append(
        {
            "title": request.opportunity.program.name,
            "url": reverse("program:home", args=(org_slug,)),
        }
    )
    path.extend(
        [
            {"title": "Opportunities", "url": reverse("opportunity:list", args=(org_slug,))},
            {"title": request.opportunity.name, "url": reverse("opportunity:detail", args=(org_slug, opp_id))},
            {"title": "Suspended Users", "url": request.path},
        ]
    )
    return render(
        request, "opportunity/suspended_users.html", dict(table=table, opportunity=request.opportunity, path=path)
    )


@opp_standard_access_required
@opportunity_required
def opportunity_user_invite(request, org_slug=None, opp_id=None):
    if request.opportunity.has_ended:
        messages.error(request, _("This opportunity has ended. You cannot invite more workers."))
        return redirect("opportunity:detail", request.org.slug, opp_id)
    form = OpportunityUserInviteForm(data=request.POST or None, opportunity=request.opportunity)
    if form.is_valid():
        users = form.cleaned_data["users"]
        if users:
            add_connect_users.delay(users, request.opportunity.pk)
        return redirect("opportunity:detail", request.org.slug, opp_id)
    return render(
        request,
        "components/form.html",
        dict(title=f"{request.org.slug} - {request.opportunity.name}", form_title="Invite Connect Workers", form=form),
    )


@opp_standard_access_required
@opportunity_required
@require_manual_visit_verification
def user_visit_review(request, org_slug, opp_id):
    if request.POST and request.is_opportunity_pm:
        review_status = request.POST.get("review_status").lower()
        updated_reviews = request.POST.getlist("pk")
        user_visits = UserVisit.objects.filter(pk__in=updated_reviews).exclude(review_status=VisitReviewStatus.agree)
        if review_status in [VisitReviewStatus.agree.value, VisitReviewStatus.disagree.value]:
            users = [visit.user for visit in user_visits]
            user_visits.update(review_status=review_status, review_status_modified_date=now())
            update_payment_accrued(opportunity=request.opportunity, users=users)

    return HttpResponse(status=200, headers={"HX-Trigger": "reload_table"})


@opp_standard_access_required
@opportunity_required
def payment_report(request, org_slug, opp_id):
    usd = request.GET.get("usd", False)

    amount_field = "amount"
    currency = request.opportunity.currency_code
    if usd:
        amount_field = "amount_usd"
        currency = "USD"

    total_paid_users = Payment.objects.filter(
        opportunity_access__opportunity=request.opportunity, organization__isnull=True
    ).aggregate(total=Sum(amount_field))["total"] or Decimal("0.00")
    total_paid_nm = Payment.objects.filter(
        organization=request.opportunity.organization, invoice__opportunity=request.opportunity
    ).aggregate(total=Sum(amount_field))["total"] or Decimal("0.00")
    data, total_user_payment_accrued, total_nm_payment_accrued = get_payment_report_data(request.opportunity, usd)
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
            opportunity=request.opportunity,
            cards=cards,
        ),
    )


@opp_standard_access_required
@require_POST
@csrf_exempt
@opportunity_required
def delete_user_invites(request, org_slug, opp_id):
    invite_ids = request.POST.getlist("user_invite_ids")
    if not invite_ids:
        return HttpResponseBadRequest()

    user_invites = (
        UserInvite.objects.filter(id__in=invite_ids, opportunity=request.opportunity)
        .exclude(status=UserInviteStatus.accepted)
        .select_related("opportunity_access")
    )

    opportunity_access_ids = [invite.opportunity_access.id for invite in user_invites if invite.opportunity_access]
    deleted_count = user_invites.count()
    cannot_delete_count = len(invite_ids) - deleted_count
    user_invites.delete()
    OpportunityAccess.objects.filter(id__in=opportunity_access_ids).delete()

    event = Event(
        name="user_invites_deleted",
        params={
            GA_CUSTOM_DIMENSIONS.TOTAL.value: len(invite_ids),
            GA_CUSTOM_DIMENSIONS.SUCCESS_COUNT.value: deleted_count,
        },
    )
    send_event_to_ga(request, event)

    if deleted_count > 0:
        messages.success(request, mark_safe(f"Successfully deleted {deleted_count} invite(s)."))
    if cannot_delete_count > 0:
        messages.warning(
            request,
            mark_safe(f"Cannot delete {cannot_delete_count} invite(s). Accepted invites cannot be deleted."),
        )

    redirect_url = reverse("opportunity:worker_list", args=(request.org.slug, opp_id))
    return HttpResponse(headers={"HX-Redirect": redirect_url})


@opp_standard_access_required
@require_POST
@opportunity_required
def resend_user_invites(request, org_slug, opp_id):
    if request.opportunity.has_ended:
        messages.error(request, _("This opportunity has ended. You cannot resend invites."))
        redirect_url = reverse("opportunity:detail", args=(org_slug, opp_id))
        return HttpResponse(headers={"HX-Redirect": redirect_url})
    if not request.opportunity.is_setup_complete:
        messages.error(request, _("Please finish setting up the opportunity before resending invites."))
        redirect_url = reverse("opportunity:detail", args=(org_slug, opp_id))
        return HttpResponse(headers={"HX-Redirect": redirect_url})
    invite_ids = request.POST.getlist("user_invite_ids")
    if not invite_ids:
        return HttpResponseBadRequest()

    user_invites = UserInvite.objects.filter(id__in=invite_ids, opportunity=request.opportunity).select_related(
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
            access, __ = OpportunityAccess.objects.get_or_create(user=user, opportunity=request.opportunity)
            invite_user.delay(user.id, access.pk)
            resent_count += 1

    if not_found_phone_numbers:
        found_user_list = fetch_users(not_found_phone_numbers)
        for found_user in found_user_list:
            not_found_phone_numbers.remove(found_user.phone_number)
            update_user_and_send_invite(found_user, request.opportunity.pk)
            resent_count += 1

    event = Event(
        name="user_invites_resent",
        params={
            GA_CUSTOM_DIMENSIONS.TOTAL.value: len(invite_ids),
            GA_CUSTOM_DIMENSIONS.SUCCESS_COUNT.value: resent_count,
        },
    )
    send_event_to_ga(request, event)

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


@opp_standard_access_required
@require_POST
@opportunity_required
def sync_deliver_units(request, org_slug, opp_id):
    status = HTTPStatus.OK
    message = "Delivery unit sync completed."
    try:
        create_learn_modules_and_deliver_units(request.opportunity.pk)
    except AppNoBuildException:
        status = HTTPStatus.BAD_REQUEST
        message = _("Failed to retrieve updates. No available build at the moment.")
    except (CommCareHQAPIException, httpx.RequestError, httpx.TimeoutException, httpx.ConnectError):
        logger.exception("Failed to sync delivery units for opportunity %s", opp_id)
        status = HTTPStatus.BAD_GATEWAY
        message = _("Failed to retrieve updates from CommCare HQ. Please try again.")

    return HttpResponse(content=message, status=status)


def _can_manage_tasks(request, opportunity):
    """Permission to create, edit, or delete tasks."""
    return is_opportunity_pm(request, opportunity)


def _can_edit_tasks(request, opportunity):
    return opportunity_access_level_from_request(request, opportunity) >= AccessLevel.STANDARD


def _task_redirect_url(request, org_slug, opp_id):
    user_id = request.GET.get("user", "")
    if request.GET.get("next") == _NEXT_WORKER_TASKS and user_id:
        url = reverse("opportunity:user_tasks_list", args=(org_slug, opp_id))
        return f"{url}?{urlencode({'user': user_id})}"
    return reverse("opportunity:assigned_task_list", args=(org_slug, opp_id))


@opp_view_access_required
@opportunity_required
def user_visit_details(request, org_slug, opp_id, pk):
    user_visit = get_object_or_404(UserVisit, user_visit_id=pk, opportunity=request.opportunity)
    verification_flags_config = request.opportunity.opportunityverificationflags

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
            UserVisit.objects.filter(opportunity=request.opportunity)
            .exclude(pk=user_visit.pk)
            .annotate(
                lat_val=Cast(SplitPart("location", Value(" "), Value(1)), FloatField()),
                lon_val=Cast(SplitPart("location", Value(" "), Value(2)), FloatField()),
            )
            .filter(
                lat_val__range=(lat - lat_delta, lat + lat_delta),
                lon_val__range=(lon - lon_delta, lon + lon_delta),
            )
            .select_related("user", "opportunity")
        )

        for loc in qs:
            if not loc.location:
                continue
            try:
                other_lat, other_lon, *_ = loc.location.split()
                dist = distance.distance((lat, lon), (float(other_lat), float(other_lon))).m
                if dist <= 250:
                    visit_info = {
                        "entity_name": loc.entity_name,
                        "user__name": loc.user.name,
                        "status": loc.get_status_display(),
                        "visit_date": loc.visit_date,
                        "url": reverse(
                            "opportunity:user_visit_details",
                            kwargs={
                                "org_slug": request.org.slug,
                                "opp_id": loc.opportunity.opportunity_id,
                                "pk": loc.user_visit_id,
                            },
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
            min_allowed_distance=verification_flags_config.location,
            verification_flags_config=verification_flags_config,
            flags=flags,
            flag_count=flag_count,
            attachment_flagged=attachment_flagged,
        ),
    )


@opp_view_access_required
@opportunity_required
def user_visit_data(request, org_slug, opp_id, pk):
    user_visit = get_object_or_404(
        UserVisit.objects.select_related("user", "deliver_unit"),
        user_visit_id=pk,
        opportunity=request.opportunity,
    )
    worker_url = reverse("opportunity:user_visits_list", args=[org_slug, opp_id])
    visit_date = user_visit.visit_date
    if is_aware(visit_date):
        visit_date = localtime(visit_date)
    return JsonResponse(
        {
            "visit_date": visit_date.strftime(DATE_TIME_FORMAT),
            "worker_name": escape(user_visit.user.name),
            "phone_number": escape(user_visit.user.phone_number) if user_visit.user.phone_number else None,
            "deliver_type": escape(user_visit.deliver_unit.name) if user_visit.deliver_unit else None,
            "worker_url": (
                f"{worker_url}?{urlencode({'user': user_visit.user.user_id, 'visit_id': user_visit.user_visit_id})}"
            ),
        }
    )


@opp_view_access_required
@opportunity_required
def user_task_details(request, org_slug, opp_id, pk):
    completed_task = get_object_or_404(
        AssignedTask.objects.select_related(
            "task_type__app__hq_server",
            "opportunity_access__opportunity__deliver_app",
            "assigned_by",
        ),
        assigned_task_id=pk,
        opportunity_access__opportunity=request.opportunity,
    )

    images = []
    hq_link = None
    if completed_task.xform_id:
        images = BlobMeta.objects.filter(parent_id=completed_task.xform_id, content_type__startswith="image/")
        hq_url = completed_task.task_type.app.hq_server.url
        domain = completed_task.opportunity_access.opportunity.deliver_app.cc_domain
        hq_link = f"{hq_url}/a/{domain}/reports/form_data/{completed_task.xform_id}/"

    return render(
        request,
        "opportunity/user_task_details.html",
        context=dict(
            completed_task=completed_task,
            images=images,
            hq_link=hq_link,
            can_edit_tasks=_can_edit_tasks(request, request.opportunity),
        ),
    )


@opp_view_access_required
@opportunity_required
def learn_module_table(request, org_slug=None, opp_id=None):
    data = LearnModule.objects.filter(app=request.opportunity.learn_app)
    table = LearnModuleTable(data)
    return render(request, "tables/single_table.html", {"table": table})


@opp_view_access_required
@opportunity_required
def deliver_unit_table(request, org_slug=None, opp_id=None):
    unit = DeliverUnit.objects.filter(app=request.opportunity.deliver_app)
    table = DeliverUnitTable(unit)
    return render(
        request,
        "tables/single_table.html",
        {
            "table": table,
        },
    )


@method_decorator(opp_view_access_required, name="dispatch")
class OpportunityPaymentUnitTableView(OpportunityObjectMixin, OrgContextSingleTableView):
    model = PaymentUnit
    table_class = PaymentUnitTable
    template_name = "tables/single_table.html"
    # Rendered inside the dashboard's resource modal, where base_table.html's page links would reload
    # the whole page and close the modal. Opportunities have few payment units, so show them all.
    table_pagination = False

    def get_queryset(self):
        return PaymentUnit.objects.filter(opportunity=self.get_opportunity()).prefetch_related("deliver_units")

    def get_table_kwargs(self):
        kwargs = super().get_table_kwargs()
        kwargs["org_slug"] = self.request.org.slug
        kwargs["is_program_manager"] = self.request.is_opportunity_pm
        return kwargs


@opp_view_access_required
@opportunity_required
def opportunity_funnel_progress(request, org_slug, opp_id):
    result = get_opportunity_funnel_progress(request.opportunity.pk)

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


@opp_view_access_required
@opportunity_required
def opportunity_worker_progress(request, org_slug, opp_id):
    return render(
        request,
        "opportunity/opportunity_worker_progress.html",
        {
            "allocation": get_delivery_allocation(request.opportunity),
            "payments": get_worker_payments(request.opportunity),
            "payment_unit_breakdown_url": reverse("opportunity:payment_unit_breakdown", args=(org_slug, opp_id)),
        },
    )


@opp_view_access_required
@opportunity_required
def opportunity_payment_unit_breakdown(request, org_slug, opp_id):
    return render(
        request,
        "opportunity/partials/payment_unit_breakdown.html",
        {"breakdown": get_payment_unit_breakdown(request.opportunity)},
    )


@opp_view_access_required
@opportunity_required
def opportunity_delivery_stats(request, org_slug, opp_id):
    panel_type_2 = {
        "body": "bg-brand-marigold/10 border border-brand-marigold",
        "icon_bg": "!bg-orange-300",
        "text_color": "!text-orange-500",
    }

    stats = get_opportunity_delivery_progress(request.opportunity.pk)

    worker_list_url = reverse("opportunity:worker_list", args=(org_slug, opp_id))
    status_url = f"{worker_list_url}?{urlencode({'sort': '-last_active'})}"
    delivery_url = reverse("opportunity:worker_deliver", args=(org_slug, opp_id))
    payment_url = reverse("opportunity:worker_payments", args=(org_slug, opp_id))

    microplanning_url = reverse("microplanning:microplanning_home", args=(org_slug, opp_id))

    deliveries_panels = [
        {
            "icon": "fa-clipboard-list",
            "name": _("Services Delivered"),
            "status": _("Total"),
            "value": header_with_tooltip(stats.total_deliveries, _("Total delivered so far")),
            "url": f"{delivery_url}?{urlencode({'sort': '-last_active'})}",
            "incr": stats.deliveries_from_yesterday,
        },
    ]
    if flag_is_active(request, MICROPLANNING):
        deliveries_panels.append(
            {
                "icon": "fa-map-location-dot",
                "name": _("View Progress Map"),
                "status": "",
                "value": "",
                "url": microplanning_url,
            }
        )
    if flag_is_active(request, WEEKLY_PERFORMANCE_REPORT):
        deliveries_panels.append(
            {
                "icon": "fa-magnifying-glass",
                "name": _("Audit Opportunity"),
                "status": "",
                "value": "",
                "url": reverse(
                    "opportunity:audit:audit_report_list",
                    kwargs={"org_slug": org_slug, "opp_id": opp_id},
                ),
            }
        )

    tasks_url = reverse("opportunity:assigned_task_list", args=(org_slug, opp_id))
    connect_worker_panels = [
        {
            "icon": "fa-user-group",
            "name": _("Connect Workers"),
            "status": "",
            "value": "",
            "url": status_url,
        },
        {
            "icon": "fa-clipboard-list",
            "name": _("Connect Workers"),
            "status": _("Inactive last 3 days"),
            "url": f"{delivery_url}?{urlencode({'last_active': '3'})}",
            "value": header_with_tooltip(
                stats.inactive_workers, _("Did not submit a Learn or Deliver form in the last 3 days")
            ),
            **panel_type_2,
        },
    ]
    if (
        switch_is_active(WORKER_VISITS_TASKS)
        and TaskType.objects.filter(opportunity=request.opportunity, is_active=True).exists()
    ):
        connect_worker_panels.append(
            {
                "icon": "fa-list-check",
                "name": _("Tasks Assigned to Connect Workers"),
                "status": "",
                "value": stats.active_tasks_count,
                "url": tasks_url,
            }
        )

    opp_stats = [
        {
            "title": _("Connect Workers"),
            "sub_heading": "",
            "value": "",
            "panels": connect_worker_panels,
        },
        {
            "title": _("Services Delivered"),
            "sub_heading": _("Last Delivery"),
            "value": stats.most_recent_delivery or "--",
            "panels": deliveries_panels,
        },
        {
            "title": _("Worker Payments"),
            "sub_heading": _("Last Payment"),
            "value": stats.recent_payment or "--",
            "panels": [
                {
                    "icon": "fa-hand-holding-dollar",
                    "name": _("Payments"),
                    "status": _("Earned"),
                    "value": header_with_tooltip(
                        amount_with_currency(stats.total_accrued, request.opportunity.currency_code),
                        _("Worker payment accrued based on approved service deliveries"),
                    ),
                    "url": payment_url,
                    "incr": stats.accrued_since_yesterday,
                },
                {
                    "icon": "fa-hand-holding-droplet",
                    "name": _("Payments"),
                    "status": _("Due"),
                    "value": header_with_tooltip(
                        amount_with_currency(stats.payments_due, request.opportunity.currency_code),
                        _("Worker payments earned but yet unpaid"),
                    ),
                },
            ],
        },
    ]

    return render(request, "opportunity/opportunity_delivery_stat.html", {"opp_stats": opp_stats})


@login_required
@require_POST
def add_api_key(request, org_slug):
    fixed_hq_server = _fixed_hq_server(request)
    form = HQApiKeyCreateForm(data=request.POST, auto_id="api_key_form_id_for_%s", fixed_hq_server=fixed_hq_server)

    headers = {}
    if form.is_valid():
        api_key = form.save(commit=False)
        api_key.user = request.user
        api_key.save()
        form = HQApiKeyCreateForm(auto_id="api_key_form_id_for_%s", fixed_hq_server=fixed_hq_server)
        # Only a saved key closes the modal and reloads the key select; a rejected one keeps its errors on screen.
        headers["HX-Trigger"] = "reload_api_keys"
    return HttpResponse(render_crispy_form(form), headers=headers)


def _fixed_hq_server(request):
    """The server the add-key modal is pinned to, passed as `?hq_server=` by pages that pin it."""
    server_id = request.GET.get("hq_server")
    if not server_id or not server_id.isdigit():
        return None
    return HQServer.objects.filter(pk=server_id).first()


@login_required
@require_GET
@opp_standard_access_required
@opportunity_required
def visit_export_count(request, org_slug, opp_id):
    from_date_str = request.GET.get("from_date")
    if not from_date_str:
        return HttpResponse({"error": "Please select a From Date first."}, status=400)

    to_date_str = request.GET.get("to_date")
    status = request.GET.get("status", None)
    review_export = request.GET.get("review_export") == "true"
    format = request.GET.get("format", "csv")

    from_date = datetime.date.fromisoformat(from_date_str)
    to_date = datetime.date.fromisoformat(to_date_str) if to_date_str else datetime.date.today()
    from_date, to_date = get_start_end_date_range_with_time(from_date, to_date)
    visits = UserVisit.objects.filter(
        opportunity_id=request.opportunity.pk, visit_date__gte=from_date, visit_date__lte=to_date
    )

    if review_export:
        visits = visits.filter(review_created_on__isnull=False)
        if status in VisitReviewStatus:
            visits = visits.filter(review_status=status)
    else:
        if status in VisitValidationStatus:
            visits = visits.filter(status=status)

    count = visits.count()

    message_class = "text-green-600"
    message = f"{count:,} visits match your filters."
    button_disabled = ""

    if format == "xlsx" and count > EXPORT_ROW_LIMIT:
        button_disabled = "disabled"
        message = (
            f"You have {count} visits matching your filters. "
            f"The maximum export limit for Excel is {EXPORT_ROW_LIMIT}. Please narrow your filters."
        )
        message_class = "text-red-600"

    html = format_html(
        """
        <div class='{message_class} mb-3'>{message}</div>
        <button id="export-submit-btn"
                type="submit"
                {button_disabled}
                class="button button-md primary-dark"
                hx-swap-oob="true">
            <i class="bi bi-filetype-xls"></i>
            Export
        </button>
        """,
        message_class=message_class,
        message=message,
        button_disabled=button_disabled,
    )

    return HttpResponse(html)


@method_decorator(opp_view_access_required, name="dispatch")
class AssignedTaskListView(OpportunityObjectMixin, FilterMixin, OrgContextSingleTableView):
    template_name = "opportunity/assigned_task_list.html"
    table_class = AssignedTaskListTable
    paginate_by = DEFAULT_PAGE_SIZE
    filter_class = AssignedTaskFilterSet

    def get_paginate_by(self, table_data):
        return get_validated_page_size(self.request)

    def get_filter_kwargs(self):
        return {
            "queryset": self.get_queryset(),
            "request": self.request,
            "opportunity": self.get_opportunity(),
        }

    def get_table_kwargs(self):
        kwargs = super().get_table_kwargs()
        kwargs["opp_id"] = self.get_opportunity().opportunity_id
        kwargs["can_edit_tasks"] = _can_edit_tasks(self.request, self.get_opportunity())
        kwargs["can_delete_tasks"] = _can_manage_tasks(self.request, self.get_opportunity())
        return kwargs

    def get_table_data(self):
        return self._get_filter().qs

    def get_queryset(self):
        opportunity = self.get_opportunity()
        return (
            AssignedTask.objects.filter(opportunity_access__opportunity=opportunity)
            .select_related("task_type", "opportunity_access__user", "assigned_by")
            .order_by("-date_created")
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        opportunity = self.get_opportunity()

        counts = AssignedTask.objects.filter(opportunity_access__opportunity=opportunity).aggregate(
            total_tasks=Count("id"),
            open_tasks=Count("id", filter=Q(status=AssignedTaskStatus.ASSIGNED)),
            complete_tasks=Count("id", filter=Q(status=AssignedTaskStatus.COMPLETED)),
        )

        context["opportunity"] = opportunity
        context.update(counts)
        context.update(self.get_filter_context())

        context["path"] = [
            {"title": "Opportunities", "url": reverse("opportunity:list", kwargs={"org_slug": self.request.org.slug})},
            {
                "title": opportunity.name,
                "url": reverse("opportunity:detail", args=(self.request.org.slug, opportunity.opportunity_id)),
            },
            {"title": "Task List"},
        ]

        can_manage_tasks = _can_manage_tasks(self.request, opportunity)
        context["can_manage_tasks"] = can_manage_tasks
        if can_manage_tasks:
            context["create_task_form"] = CreateTaskForm(opportunity=opportunity, user=self.request.user)
            context["create_task_url"] = reverse(
                "opportunity:create_task", args=(self.request.org.slug, opportunity.opportunity_id)
            )

        return context


@method_decorator(opp_standard_access_required, name="dispatch")
class EditAssignedTask(OpportunityObjectMixin, UpdateView):
    template_name = "opportunity/edit_assigned_task_form.html"
    form_class = EditAssignedTaskForm
    model = AssignedTask

    def get_object(self, queryset=None):
        return get_object_or_404(
            self.model.objects.select_related("task_type"),
            pk=self.kwargs["pk"],
            opportunity_access__opportunity=self.get_opportunity(),
            status=AssignedTaskStatus.ASSIGNED,
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["hx_post_url"] = self.request.path
        task = self.object
        context["task_type_name"] = task.task_type.name
        context["current_due_date"] = task.due_date.isoformat()
        return context

    def form_valid(self, form):
        if form.has_changed():
            task = form.save(commit=False)
            reason = form.cleaned_data.get("reason", "")
            with pghistory.context(
                reason=reason,
                username=self.request.user.username,
                user_email=self.request.user.email,
            ):
                task.save(update_fields=["due_date"])
            messages.success(self.request, _("Task updated successfully."))
        redirect_url = self.request.headers.get(
            "HX-Current-URL",
            _task_redirect_url(self.request, self.kwargs["org_slug"], self.kwargs["opp_id"]),
        )
        return HttpResponse(headers={"HX-Redirect": redirect_url})


@require_POST
@opportunity_pm_required
@opportunity_required
def create_task(request, org_slug, opp_id):
    opportunity = request.opportunity
    access = None
    if request.GET.get("next") == _NEXT_WORKER_TASKS:
        user_id = request.GET.get("user")
        if user_id:
            access = OpportunityAccess.objects.filter(opportunity=opportunity, user__user_id=user_id).first()
    form = CreateTaskForm(request.POST, opportunity=opportunity, access=access, user=request.user)
    if not form.is_valid():
        return render(
            request,
            "tasks/new_task_modal.html",
            {
                "form": form,
                "modal_name": "showCreateTaskModal",
                "create_task_url": request.get_full_path(),
            },
        )

    task = form.cleaned_data["task"]
    access = form.cleaned_data["access"]
    due_date = form.cleaned_data["due_date"]

    try:
        AssignedTask.assign(
            task_type=task,
            opportunity_access=access,
            due_date=due_date,
            assigned_by=request.user,
        )
    except TaskAlreadyAssignedError:
        messages.error(request, _("This task type is already assigned to the selected worker."))
    except CommCareHQAPIException as e:
        logger.exception(f"CommCareHQ task creation failed: {str(e)}")
        messages.error(request, _("Task creation failed: could not update CommCare HQ. Please try again."))
    except OcsApiError as e:
        logger.exception(f"OCS task creation failed: {str(e)}")
        messages.error(request, _("Task creation failed: could not start the chatbot session. Please try again."))
    else:
        messages.success(request, _("Task created successfully."))
    redirect_url = _task_redirect_url(request, org_slug, opp_id)
    return HttpResponse(headers={"HX-Redirect": redirect_url})


@require_POST
@opportunity_pm_required
@opportunity_required
def delete_tasks(request, org_slug, opp_id):
    try:
        task_ids = [int(tid) for tid in request.POST.getlist("task_ids")]
        if not task_ids:
            raise ValueError
    except (TypeError, ValueError):
        return HttpResponseBadRequest()

    redirect_url = _task_redirect_url(request, org_slug, opp_id)

    try:
        deleted_count = AssignedTask.bulk_delete(task_ids, request.opportunity)
    except CommCareHQAPIException:
        logger.exception("Task deletion failed: could not update CommCare HQ for opportunity %s", opp_id)
        messages.error(request, _("Task deletion failed: could not update CommCare HQ. Please try again."))
    except ListTooLongError:
        logger.exception("Task deletion failed: too many tasks queued for opportunity %s", opp_id)
        messages.error(request, _("Too many tasks queued for deletion. Please select a smaller batch."))
    else:
        if deleted_count:
            messages.success(request, _("Successfully deleted %(count)d task(s).") % {"count": deleted_count})
    return HttpResponse(headers={"HX-Redirect": redirect_url})
