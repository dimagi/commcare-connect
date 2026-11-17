"""Assigned tasks: task type configuration, the opportunity task list, and creating, editing and deleting tasks."""

import logging
from urllib.parse import urlencode, urlparse, urlunsplit

import pghistory
from django.contrib import messages
from django.db.models import Count, Q
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.translation import gettext as _
from django.views import View
from django.views.decorators.http import require_POST
from django.views.generic import TemplateView, UpdateView
from django_tables2 import RequestConfig

from commcare_connect.opportunity.exceptions import ListTooLongError, TaskAlreadyAssignedError
from commcare_connect.opportunity.filters import (
    AssignedTaskFilterSet,
    FilterMixin,
)
from commcare_connect.opportunity.forms.assigned_tasks import (
    AddTaskTypeForm,
    CreateTaskForm,
    EditAssignedTaskForm,
    EditTaskTypeForm,
)
from commcare_connect.opportunity.models import (
    AssignedTask,
    AssignedTaskStatus,
    BlobMeta,
    OpportunityAccess,
    TaskType,
)
from commcare_connect.opportunity.tables.assigned_tasks import AssignedTaskListTable, TaskTable
from commcare_connect.opportunity.views import (
    OpportunityObjectMixin,
    OrgContextSingleTableView,
)
from commcare_connect.organization.decorators import (
    opp_standard_access_required,
    opp_view_access_required,
    opportunity_pm_required,
    opportunity_required,
)
from commcare_connect.program.utils import (
    AccessLevel,
    is_opportunity_pm,
    opportunity_access_level_from_request,
)
from commcare_connect.utils.commcarehq_api import CommCareHQAPIException
from commcare_connect.utils.oauth_tokens import TokenRefreshError
from commcare_connect.utils.ocs_api import OcsApiError, list_chatbots, user_has_connected_ocs
from commcare_connect.utils.tables import (
    DEFAULT_PAGE_SIZE,
    get_validated_page_size,
)

logger = logging.getLogger(__name__)


_NEXT_WORKER_TASKS = "worker_tasks"


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
