"""Worker pages: the Connect Workers tabs and the per-worker visits, tasks, learn and payment pages."""

import datetime
from collections import Counter, defaultdict
from functools import cached_property
from urllib.parse import urlencode, urlparse

from celery.result import AsyncResult
from django.conf import settings
from django.contrib import messages
from django.db.models import Count, DecimalField, Max, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views import View
from django.views.generic import TemplateView
from django_tables2 import RequestConfig, SingleTableView
from waffle import flag_is_active, switch_is_active

from commcare_connect.flags.flag_names import MICROPLANNING
from commcare_connect.flags.switch_names import WORKER_VISITS_TASKS
from commcare_connect.opportunity.filters import (
    DeliverFilterSet,
    FilterMixin,
    TasksFilterSet,
    UserTasksFilterSet,
)
from commcare_connect.opportunity.forms import (
    CreateTaskForm,
    ExportFormatForm,
    VisitExportForm,
)
from commcare_connect.opportunity.helpers import (
    get_worker_deliveries,
    get_worker_learn_table_data,
    get_worker_table_data,
    get_worker_tasks_base_queryset,
    get_worker_work_area_table_data,
)
from commcare_connect.opportunity.models import (
    AssignedTask,
    AssignedTaskStatus,
    CompletedModule,
    CompletedWork,
    CompletedWorkStatus,
    OpportunityAccess,
    Payment,
    UserInvite,
    UserVisit,
    VisitReviewStatus,
    VisitValidationStatus,
)
from commcare_connect.opportunity.tables.worker import (
    UserVisitVerificationTable,
    WorkerCompletedTaskTable,
    WorkerDeliveryTable,
    WorkerLearnStatusTable,
    WorkerLearnTable,
    WorkerPaymentsTable,
    WorkerStatusTable,
    WorkerTasksTable,
    WorkerVisitTable,
    WorkerWorkAreaTable,
)
from commcare_connect.opportunity.views import (
    PAYMENT_IMPORT_CLAIMED_SESSION_KEY,
    OpportunityObjectMixin,
    _can_manage_tasks,
    claim_task_outcome,
    get_export_task_id,
)
from commcare_connect.organization.decorators import (
    opp_view_access_required,
    opportunity_required,
)
from commcare_connect.program.utils import (
    AccessLevel,
    opportunity_access_level_from_request,
)
from commcare_connect.utils.celery import (
    CELERY_TASK_FAILURE,
    CELERY_TASK_SUCCESS,
    get_task_progress_message,
)
from commcare_connect.utils.flags import FlagLabels
from commcare_connect.utils.tables import (
    get_duration_min,
    get_validated_page_size,
)

PAYMENT_IMPORT_TASK_PARAM = "payment_import_task_id"


@method_decorator(opp_view_access_required, name="dispatch")
class WorkerPageView(OpportunityObjectMixin, TemplateView):
    page_title = None

    def dispatch(self, request, *args, **kwargs):
        self.opportunity = self.get_opportunity()
        user_id = request.GET.get("user")
        self.opportunity_access = (
            (
                OpportunityAccess.objects.filter(opportunity=self.opportunity, user__user_id=user_id)
                .select_related("user")
                .first()
            )
            if user_id
            else None
        )
        if not self.opportunity_access:
            raise Http404("A valid worker must be specified.")
        return super().dispatch(request, *args, **kwargs)

    def get_path(self):
        org_slug = self.kwargs["org_slug"]
        opp_id = self.kwargs["opp_id"]
        path = []
        path.append({"title": "Programs", "url": reverse("program:home", args=(org_slug,))})
        path.append(
            {
                "title": self.opportunity.program.name,
                "url": reverse("program:home", args=(org_slug,)),
            }
        )
        path.extend(
            [
                {"title": "Opportunities", "url": reverse("opportunity:list", args=(org_slug,))},
                {"title": self.opportunity.name, "url": reverse("opportunity:detail", args=(org_slug, opp_id))},
                {"title": "Connect Workers", "url": reverse("opportunity:worker_deliver", args=(org_slug, opp_id))},
                {"title": self.page_title, "url": self.request.path},
            ]
        )
        return path

    def get_worker_kpi_context(self):
        visits_queryset = UserVisit.objects.filter(opportunity_access=self.opportunity_access)
        counts = get_user_visit_counts(self.opportunity, visits_queryset)

        flagged_info = defaultdict(lambda: {"name": "", "approved": 0, "pending": 0, "rejected": 0})
        for visit in visits_queryset.filter(flagged=True, flag_reason__isnull=False):
            for flag, _description in visit.flag_reason.get("flags", []):
                flag_label = FlagLabels.get_label(flag)
                if visit.status == VisitValidationStatus.approved:
                    if visit.review_created_on is not None:
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

        last_payment_details = (
            Payment.objects.filter(opportunity_access=self.opportunity_access)
            .select_related("opportunity_access__user")
            .order_by("-date_paid")
            .first()
        )
        pending_tasks_count = AssignedTask.objects.filter(
            opportunity_access=self.opportunity_access,
            status=AssignedTaskStatus.ASSIGNED,
        ).count()
        tasks_url = reverse(
            "opportunity:user_tasks_list", args=(self.request.org.slug, self.opportunity.opportunity_id)
        )
        pending_tasks_url = f"{tasks_url}?{urlencode({'user': self.opportunity_access.user.user_id, 'task_status': AssignedTaskStatus.ASSIGNED})}"  # noqa: E501
        return {
            "counts": counts,
            "flagged_info": flagged_info.values(),
            "last_payment_details": last_payment_details,
            "pending_tasks_count": pending_tasks_count,
            "pending_tasks_url": pending_tasks_url,
        }

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(self.get_worker_kpi_context())
        context.update(
            {
                "opportunity": self.opportunity,
                "opportunity_access": self.opportunity_access,
                "path": self.get_path(),
                "has_suspension_perm": self.request.is_opportunity_pm,
            }
        )
        return context


class UserVisitVerificationView(WorkerPageView):
    template_name = "opportunity/user_visit_verification.html"
    page_title = gettext_lazy("Visits")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["MAPBOX_TOKEN"] = settings.MAPBOX_TOKEN
        context["show_worker_tasks_tabs"] = switch_is_active(WORKER_VISITS_TASKS)
        context["visit_id"] = self.request.GET.get("visit_id")
        return context


class UserTasksView(WorkerPageView, FilterMixin):
    template_name = "opportunity/user_tasks.html"
    page_title = gettext_lazy("Tasks")
    filter_class = UserTasksFilterSet

    def get_filter_kwargs(self):
        return {
            "queryset": AssignedTask.objects.none(),
            "request": self.request,
            "opportunity": self.opportunity,
        }

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        can_manage_tasks = _can_manage_tasks(self.request, self.opportunity)
        context["can_manage_tasks"] = can_manage_tasks
        context.update(self.get_filter_context())
        if can_manage_tasks:
            context["create_task_form"] = CreateTaskForm(
                opportunity=self.opportunity, access=self.opportunity_access, user=self.request.user
            )
            create_url = reverse(
                "opportunity:create_task", args=(self.request.org.slug, self.opportunity.opportunity_id)
            )
            context["create_task_url"] = (
                f"{create_url}?{urlencode({'next': 'worker_tasks', 'user': self.opportunity_access.user.user_id})}"
            )

            delete_url = reverse(
                "opportunity:delete_tasks", args=(self.request.org.slug, self.opportunity.opportunity_id)
            )
            context["delete_tasks_url"] = (
                f"{delete_url}?{urlencode({'next': 'worker_tasks', 'user': self.opportunity_access.user.user_id})}"
            )
        return context


@method_decorator(opp_view_access_required, name="dispatch")
class WorkerTableView(OpportunityObjectMixin, SingleTableView):
    redirect_url_name = None  # subclasses must set this to the parent page URL name

    def get_paginate_by(self, table_data):
        return get_validated_page_size(self.request)

    def dispatch(self, request, *args, **kwargs):
        self.opportunity = self.get_opportunity()
        response = super().dispatch(request, *args, **kwargs)
        url = reverse(self.redirect_url_name, args=[request.org.slug, self.kwargs["opp_id"]])
        query_params = request.GET.urlencode()
        response["HX-Replace-Url"] = f"{url}?{query_params}" if query_params else url
        return response

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["opportunity"] = self.opportunity
        return context


class WorkerCompletedTaskTableView(WorkerTableView, FilterMixin):
    model = AssignedTask
    table_class = WorkerCompletedTaskTable
    template_name = "opportunity/worker_visit_table.html"
    redirect_url_name = "opportunity:user_tasks_list"
    filter_class = UserTasksFilterSet

    def get_filter_kwargs(self):
        queryset = AssignedTask.objects.filter(opportunity_access__opportunity=self.opportunity).select_related(
            "task_type", "assigned_by", "opportunity_access__opportunity"
        )
        user_id = self.request.GET.get("user")
        if user_id:
            queryset = queryset.filter(opportunity_access__user__user_id=user_id)
        return {
            "queryset": queryset,
            "request": self.request,
            "opportunity": self.opportunity,
        }

    def get_table_kwargs(self):
        kwargs = super().get_table_kwargs()
        kwargs["organization"] = self.request.org
        kwargs["can_manage_tasks"] = _can_manage_tasks(self.request, self.opportunity)
        return kwargs

    def get_queryset(self):
        return self._get_filter().qs.order_by("-date_created")


def get_user_visit_counts(opportunity, queryset):
    user_visit_counts = queryset.aggregate(
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
        approved=Count("id", filter=Q(status=VisitValidationStatus.approved)),
        pending=Count("id", filter=Q(status__in=[VisitValidationStatus.pending, VisitValidationStatus.duplicate])),
        rejected=Count("id", filter=Q(status=VisitValidationStatus.rejected)),
        flagged=Count("id", filter=Q(flagged=True)),
        all=Count("*"),
    )
    return user_visit_counts


class WorkerVisitTableView(WorkerTableView):
    model = UserVisit
    table_class = WorkerVisitTable
    template_name = "opportunity/worker_visit_table.html"
    redirect_url_name = "opportunity:user_visits_list"

    def get_table_kwargs(self):
        kwargs = super().get_table_kwargs()
        kwargs["organization"] = self.request.org
        kwargs["is_opportunity_pm"] = self.request.is_opportunity_pm
        kwargs["highlighted_visit_id"] = self.request.GET.get("visit_id")
        return kwargs

    def get_queryset(self):
        queryset = UserVisit.objects.filter(opportunity=self.opportunity).select_related("user", "opportunity")
        user_id = self.request.GET.get("user")
        if user_id:
            queryset = queryset.filter(user__user_id=user_id)
        return queryset.order_by("visit_date", "pk")

    def get_table(self, **kwargs):
        table = super().get_table(**kwargs)
        visit_id = self.request.GET.get("visit_id")
        if visit_id and "page" not in self.request.GET:
            page = self._get_visit_page(visit_id)
            if page:
                table.paginate(page=page, per_page=get_validated_page_size(self.request))
        return table

    def _get_visit_page(self, visit_id):
        queryset = self.get_queryset()
        target = queryset.filter(user_visit_id=visit_id).first()
        if not target:
            return None
        preceding_count = queryset.filter(
            Q(visit_date__lt=target.visit_date) | Q(visit_date=target.visit_date, pk__lt=target.pk)
        ).count()
        return preceding_count // get_validated_page_size(self.request) + 1


class VisitVerificationTableView(WorkerVisitTableView):
    table_class = UserVisitVerificationTable
    template_name = "opportunity/user_visit_verification_table.html"
    exclude_columns = []

    def get_table(self, **kwargs):
        kwargs["exclude"] = self.exclude_columns
        self.table = super().get_table(**kwargs)
        return self.table

    def get_tab_display(self, **kwargs):
        user_visit_counts = get_user_visit_counts(self.opportunity, self.filter_queryset)
        tabs_to_labels = {
            "all": _("All"),
            "approved": _("Approved"),
            "rejected": _("Rejected"),
            "pending": _("Pending NM Review"),
            "pending_review": _("Pending PM Review"),
            "agree": _("Agree") if self.request.is_opportunity_pm else _("Approved"),
            "disagree": _("Disagree") if self.request.is_opportunity_pm else _("Revalidate"),
        }
        tabs_to_display = self.tabs
        tabs = []
        for tab in tabs_to_display:
            tabs.append({"name": tab, "label": tabs_to_labels[tab], "count": user_visit_counts.get(tab, 0)})
        return tabs

    @cached_property
    def tabs(self):
        opportunity = self.get_opportunity()
        if opportunity.automatic_visit_verification:
            if self.request.is_opportunity_pm:
                return ["all"]
            else:
                return ["approved", "rejected", "all"]

        if self.request.is_opportunity_pm:
            return ["pending_review", "disagree", "agree", "all"]
        else:
            return ["pending", "pending_review", "disagree", "agree", "rejected", "all"]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["tabs"] = self.get_tab_display()
        persisted_filters = []
        for name, values in self.request.GET.lists():
            if name in {"page", "filter_status", "sort"}:
                continue
            for value in values:
                if value in ("", None):
                    continue
                persisted_filters.append((name, value))
        context["persisted_filters"] = persisted_filters
        return context

    def get_queryset(self):
        self.exclude_columns = []
        self.filter_queryset = super().get_queryset()

        requested_status = self.request.GET.get("filter_status")
        allowed = set(self.tabs)
        self.filter_status = requested_status if requested_status in allowed else None
        queryset = self.filter_queryset
        if self.filter_status == "pending":
            queryset = queryset.filter(status__in=[VisitValidationStatus.pending, VisitValidationStatus.duplicate])
            self.exclude_columns = ["last_activity"]
        if self.filter_status == "approved":
            queryset = queryset.filter(status=VisitValidationStatus.approved)
        if self.filter_status == "rejected":
            queryset = queryset.filter(status=VisitValidationStatus.rejected)

        if self.filter_status == "pending_review":
            queryset = queryset.filter(
                review_status=VisitReviewStatus.pending,
                status=VisitValidationStatus.approved,
                review_created_on__isnull=False,
            )
        if self.filter_status == "disagree":
            queryset = queryset.filter(
                review_status=VisitReviewStatus.disagree,
                review_created_on__isnull=False,
            )
        if self.filter_status == "agree":
            queryset = queryset.filter(
                review_status=VisitReviewStatus.agree,
                status=VisitValidationStatus.approved,
                review_created_on__isnull=False,
            )
        return queryset


@opp_view_access_required
@opportunity_required
def visit_verification_table_view(request, org_slug, opp_id):
    if switch_is_active(WORKER_VISITS_TASKS):
        return WorkerVisitTableView.as_view()(request, org_slug=org_slug, opp_id=opp_id)
    return VisitVerificationTableView.as_view()(request, org_slug=org_slug, opp_id=opp_id)


@method_decorator(opp_view_access_required, name="dispatch")
class BaseWorkerListView(OpportunityObjectMixin, View):
    template_name = "opportunity/opportunity_worker.html"
    hx_template_name = "opportunity/workers.html"
    active_tab = "workers"
    tabs = [
        {"key": "workers", "label": gettext_lazy("Connect Workers"), "url_name": "opportunity:worker_list"},
        {"key": "learn", "label": gettext_lazy("Learn"), "url_name": "opportunity:worker_learn"},
        {"key": "deliver", "label": gettext_lazy("Deliver"), "url_name": "opportunity:worker_deliver"},
        {"key": "payments", "label": gettext_lazy("Payments"), "url_name": "opportunity:worker_payments"},
        {"key": "tasks", "label": gettext_lazy("Tasks"), "url_name": "opportunity:worker_tasks"},
    ]

    def _is_navigating_between_tabs(self, org_slug, opportunity):
        referer = self.request.headers.get("referer")
        is_tab_navigation = False
        if referer:
            path = urlparse(referer).path
            for tab in self.tabs:
                if path.endswith(reverse(tab["url_name"], args=(org_slug, opportunity.opportunity_id))):
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
            url = reverse(tab["url_name"], args=(org_slug, opportunity.opportunity_id))
            if tab["key"] == self.active_tab:
                tab_params = params
            else:
                tab_params = self.request.session.get(f"worker_tab_params:{tab['key']}", {})
            if tab_params:
                url = f"{url}?{urlencode(tab_params)}"
            tabs_with_urls.append({**tab, "url": url})

        # Label with count for workers tab
        workers_count = UserInvite.objects.filter(opportunity=opportunity).count()
        tabs_with_urls[0]["label"] = _("Connect Workers") + f" ({workers_count})"
        return tabs_with_urls

    def get(self, request, org_slug, opp_id):
        opportunity = self.get_opportunity()
        request.opportunity = opportunity
        if flag_is_active(request, MICROPLANNING):
            self.tabs = self.tabs + [
                {
                    "key": "work_areas",
                    "label": gettext_lazy("Work Area Assignments"),
                    "url_name": "opportunity:worker_work_areas",
                }
            ]
        context = self.get_context_data(opportunity, org_slug)
        context.update(self.get_extra_context(opportunity, org_slug))
        return render(
            request,
            self.hx_template_name if request.htmx else self.template_name,
            context,
        )

    def get_context_data(self, opportunity, org_slug):
        path = []
        path.append({"title": "Programs", "url": reverse("program:home", args=(org_slug,))})
        path.append(
            {
                "title": opportunity.program.name,
                "url": reverse("program:home", args=(org_slug,)),
            }
        )
        path.extend(
            [
                {"title": "Opportunities", "url": reverse("opportunity:list", args=(org_slug,))},
                {
                    "title": opportunity.name,
                    "url": reverse("opportunity:detail", args=(org_slug, opportunity.opportunity_id)),
                },
                {
                    "title": "Connect Workers",
                    "url": reverse("opportunity:worker_list", args=(org_slug, opportunity.opportunity_id)),
                },
            ]
        )

        context = {
            "path": path,
            "opportunity": opportunity,
            "active_tab": self.active_tab,
            "tabs": self.get_tabs(org_slug, opportunity),
            "export_task_id": get_export_task_id(self.request, opportunity),
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

    def _get_search_term(self):
        return self.request.GET.get("q", "").strip()

    def get_extra_context(self, opportunity, org_slug):
        context = {
            "export_form": ExportFormatForm(),
            "search_term": self._get_search_term(),
        }
        if self.request.htmx:
            # Counts for "Displaying X of Y Connect Workers"; table already built by get_context_data.
            table = self._table
            context["worker_filtered_count"] = table.paginator.count
            context["worker_total_count"] = UserInvite.objects.filter(opportunity=opportunity).count()
        return context

    def get_table(self, opportunity, org_slug):
        data = get_worker_table_data(opportunity, search_term=self._get_search_term())
        table = WorkerStatusTable(data)
        RequestConfig(self.request, paginate={"per_page": get_validated_page_size(self.request)}).configure(table)
        # Cache for get_extra_context so we can read paginator.count without requerying.
        self._table = table
        return table


class WorkerLearnView(BaseWorkerListView):
    hx_template_name = "opportunity/learn.html"
    active_tab = "learn"

    def get_table(self, opportunity, org_slug):
        data = get_worker_learn_table_data(opportunity)
        table = WorkerLearnTable(data, org_slug=org_slug, opp_id=opportunity.opportunity_id)
        RequestConfig(self.request, paginate={"per_page": get_validated_page_size(self.request)}).configure(table)
        return table


class WorkerDeliverView(BaseWorkerListView, FilterMixin):
    hx_template_name = "opportunity/deliver.html"
    active_tab = "deliver"
    filter_class = DeliverFilterSet

    def get_extra_context(self, opportunity, org_slug):
        context = {
            "visit_export_form": VisitExportForm(opportunity=opportunity, org_slug=org_slug),
            "review_visit_export_form": VisitExportForm(
                opportunity=opportunity, org_slug=org_slug, review_export=True
            ),
            "import_export_delivery_urls": {
                "export_url_for_pm": reverse(
                    "opportunity:review_visit_export",
                    args=(org_slug, opportunity.opportunity_id),
                ),
                "export_url_for_nm": reverse(
                    "opportunity:visit_export",
                    args=(org_slug, opportunity.opportunity_id),
                ),
                "import_url": reverse(
                    "opportunity:review_visit_import"
                    if self.request.is_opportunity_pm
                    else "opportunity:visit_import",
                    args=(org_slug, opportunity.opportunity_id),
                ),
            },
            "import_visit_helper_text": _(
                'The file must contain at least the "Visit ID", "Justification" and "Status" column. The import is case-insensitive.'  # noqa: E501
            ),
            "export_user_visit_title": _(
                "Import PM Review Sheet" if self.request.is_opportunity_pm else "Import Verified Visits"
            ),
        }
        context.update(self.get_filter_context())
        return context

    def get_filter_kwargs(self):
        kwargs = super().get_filter_kwargs()
        kwargs["opportunity"] = self.get_opportunity()
        return kwargs

    def get_table(self, opportunity, org_slug):
        data = get_worker_deliveries(opportunity, self.get_filter_values())
        table_kwargs = {"org_slug": org_slug, "opp_id": opportunity.opportunity_id}
        if opportunity.automatic_visit_verification:
            table_kwargs["exclude"] = ("pending",)
        table = WorkerDeliveryTable(data, **table_kwargs)
        RequestConfig(self.request, paginate={"per_page": get_validated_page_size(self.request)}).configure(table)
        return table


class WorkerPaymentsView(BaseWorkerListView):
    hx_template_name = "opportunity/payments.html"
    active_tab = "payments"
    show_import_outcome = True

    def get(self, request, org_slug, opp_id):
        # A finished import surfaces its result as a banner, or as the error modal opened by
        # get_extra_context; a running one keeps the polling progress spinner.
        if not request.htmx and self._payment_import_complete():
            self.show_import_outcome = claim_task_outcome(
                request, PAYMENT_IMPORT_CLAIMED_SESSION_KEY, self._payment_import_task_id
            )
            if self.show_import_outcome:
                self._add_payment_import_message()
        return super().get(request, org_slug, opp_id)

    @cached_property
    def _payment_import_task_id(self):
        if opportunity_access_level_from_request(self.request, self.get_opportunity()) < AccessLevel.STANDARD:
            return None
        return self.request.GET.get(PAYMENT_IMPORT_TASK_PARAM)

    @cached_property
    def _payment_import_task(self):
        task_id = self._payment_import_task_id
        if not task_id:
            return None
        task = AsyncResult(task_id)
        args = task.args or []
        if not args or args[0] != self.get_opportunity().pk:
            return None
        return task

    def _payment_import_complete(self):
        task = self._payment_import_task
        return bool(task) and task.status in (CELERY_TASK_SUCCESS, CELERY_TASK_FAILURE)

    def _add_payment_import_message(self):
        """Surface the finished import task's result as a standard banner."""
        task = self._payment_import_task
        if not task:
            return
        if task.status == CELERY_TASK_FAILURE:
            messages.error(self.request, _("The payment import failed. Please try again."))
            return
        if self._payment_import_errors():
            return
        message = get_task_progress_message(task)
        if not message:
            return
        is_error = self._payment_import_result().get("is_error")
        add_message = messages.error if is_error else messages.success
        add_message(self.request, message)

    def _payment_import_result(self):
        """The import task's progress meta, or {} when there is no task or it crashed.

        A task that failed carries the exception in `result` rather than the meta dict.
        """
        result = getattr(self._payment_import_task, "result", None)
        return result if isinstance(result, dict) else {}

    def _payment_import_errors(self):
        """Row errors from the import task, as {description: [row numbers]}."""
        return self._payment_import_result().get("errors") or {}

    def get_extra_context(self, opportunity, org_slug):
        # Only keep polling while the import is still running. Once it is complete the only thing
        # left to open is the error modal, and only for errors this load has not already shown.
        task_id = self._payment_import_task_id
        if task_id and self._payment_import_complete():
            if not (self._payment_import_errors() and self.show_import_outcome):
                task_id = None
        return {
            "export_form": ExportFormatForm(),
            "payment_import_task_id": task_id,
        }

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
            status=Subquery(UserInvite.objects.filter(opportunity_access=OuterRef("pk")).values("status")[:1]),
            last_paid=Max("payment__date_paid"),
            total_paid_d=get_payment_subquery(),
            confirmed_paid_d=get_payment_subquery(True),
        )
        table = WorkerPaymentsTable(query_set, org_slug=org_slug, opp_id=opportunity.opportunity_id)
        RequestConfig(self.request, paginate={"per_page": get_validated_page_size(self.request)}).configure(table)
        return table


class WorkerTaskView(BaseWorkerListView, FilterMixin):
    hx_template_name = "opportunity/tasks.html"
    active_tab = "tasks"
    filter_class = TasksFilterSet

    def get_filter_kwargs(self):
        return {
            "queryset": get_worker_tasks_base_queryset(self.get_opportunity()),
            "request": self.request,
            "opportunity": self.get_opportunity(),
        }

    def get_extra_context(self, opportunity, org_slug):
        return self.get_filter_context()

    def get_table(self, opportunity, org_slug):
        data = self._get_filter().qs
        table = WorkerTasksTable(data, org_slug=org_slug, opp_id=opportunity.opportunity_id)
        RequestConfig(self.request, paginate={"per_page": get_validated_page_size(self.request)}).configure(table)
        return table


class WorkerWorkAreaView(BaseWorkerListView):
    hx_template_name = "opportunity/worker_list_work_areas.html"
    active_tab = "work_areas"

    def dispatch(self, request, *args, **kwargs):
        request.opportunity = self.get_opportunity()
        if not flag_is_active(request, MICROPLANNING):
            raise Http404
        return super().dispatch(request, *args, **kwargs)

    def get_table(self, opportunity, org_slug):
        data = get_worker_work_area_table_data(opportunity)
        table = WorkerWorkAreaTable(data, org_slug=org_slug)
        RequestConfig(self.request, paginate={"per_page": get_validated_page_size(self.request)}).configure(table)
        return table


@opp_view_access_required
@opportunity_required
def worker_learn_status_view(request, org_slug, opp_id, access_id):
    access = get_object_or_404(OpportunityAccess, opportunity=request.opportunity, opportunity_access_id=access_id)
    completed_modules = CompletedModule.objects.filter(opportunity_access=access)
    total_duration = datetime.timedelta(0)
    for cm in completed_modules:
        total_duration += cm.duration
    total_duration = get_duration_min(total_duration.total_seconds())

    table = WorkerLearnStatusTable(completed_modules)

    path = [
        {"title": "Opportunities", "url": reverse("opportunity:list", kwargs={"org_slug": org_slug})},
        {"title": request.opportunity.name, "url": reverse("opportunity:detail", args=(org_slug, opp_id))},
        {
            "title": "Connect Workers",
            "url": reverse("opportunity:worker_learn", args=(org_slug, opp_id)),
        },
        {"title": access.user.name, "url": request.path},
    ]

    return render(
        request,
        "opportunity/opportunity_worker_learn.html",
        {
            "total_learn_duration": total_duration,
            "table": table,
            "access": access,
            "path": path,
            "has_suspension_perm": (request.is_opportunity_pm),
        },
    )


@opp_view_access_required
@opportunity_required
def worker_payment_history(request, org_slug, opp_id, access_id):
    access = get_object_or_404(OpportunityAccess, opportunity=request.opportunity, opportunity_access_id=access_id)
    queryset = Payment.objects.filter(opportunity_access=access).order_by("-date_paid")
    payments = queryset.values("date_paid", "amount")

    return render(
        request,
        "components/worker_page/payment_history.html",
        context=dict(access=access, payments=payments, latest_payment=queryset.first()),
    )


@opp_view_access_required
@opportunity_required
def worker_flag_counts(request, org_slug, opp_id):
    access_id = request.GET.get("access_id", None)
    filters = {}
    if access_id:
        access = get_object_or_404(OpportunityAccess, opportunity=request.opportunity, opportunity_access_id=access_id)
        filters["completed_work__opportunity_access"] = access
    else:
        filters["completed_work__opportunity_access__opportunity"] = request.opportunity

    status = request.GET.get("status", CompletedWorkStatus.pending)
    payment_unit_ids = request.GET.getlist("payment_unit_id")
    filters["completed_work__status"] = status
    if payment_unit_ids:
        filters["completed_work__payment_unit__payment_unit_id__in"] = payment_unit_ids

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
