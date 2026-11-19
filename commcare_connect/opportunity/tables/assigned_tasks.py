"""Assigned-task tables: the opportunity task list and the task type configuration table."""

import django_tables2 as tables
from django.db.models import CharField, Value
from django.db.models.functions import Coalesce, NullIf
from django.utils.html import format_html
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from commcare_connect.opportunity.models import (
    AssignedTask,
    AssignedTaskStatus,
    TaskType,
    TaskTypeModeChoices,
)
from commcare_connect.opportunity.tables import (
    OpportunityContextTable,
)
from commcare_connect.utils.tables import (
    DMYTColumn,
    IndexColumn,
    select_column,
)


class TaskStatusColumn(tables.Column):
    def render(self, value):
        if value is None:
            return "—"
        if value == AssignedTaskStatus.ASSIGNED:
            status = _("To Do")
            badge_classes = "bg-amber-100 text-amber-800"
        elif value == AssignedTaskStatus.COMPLETED:
            status = _("Complete")
            badge_classes = "bg-green-100 text-green-800"
        else:
            return str(value)
        return format_html(
            '<span class="inline-flex w-[80px] items-center justify-center px-3 py-1 rounded text-xs font-medium {}">'
            "{}</span>",
            badge_classes,
            status,
        )


_task_select_td_extra = {
    "@change": "updateSelectAll()",
    # return None instead of False to skip the attribute.
    "disabled": lambda record: True if record.status == AssignedTaskStatus.COMPLETED else None,
}


class BaseAssignedTaskTable(tables.Table):
    select = select_column(td_extra=_task_select_td_extra)
    status = TaskStatusColumn(verbose_name=gettext_lazy("Status"), accessor="status")
    assigned_date = DMYTColumn(verbose_name=gettext_lazy("Assigned Date"), accessor="date_created")
    due_date = DMYTColumn(verbose_name=gettext_lazy("Due Date"), accessor="due_date")
    assigned_by = tables.Column(
        verbose_name=gettext_lazy("Assigned By"),
        accessor="assigned_by",
        empty_values=(None,),
        default=gettext_lazy("Deleted user"),
    )

    def render_assigned_by(self, value):
        return value.name or value.email

    def order_assigned_by(self, queryset, is_descending):
        expression = Coalesce(NullIf("assigned_by__name", Value("")), "assigned_by__email", output_field=CharField())
        queryset = queryset.order_by(expression.desc() if is_descending else expression.asc())
        return queryset, True


class AssignedTaskListTable(BaseAssignedTaskTable, OpportunityContextTable):
    connect_worker = tables.Column(verbose_name=gettext_lazy("Connect Worker"), accessor="opportunity_access__user")
    phone_number = tables.Column(
        verbose_name=gettext_lazy("Phone Number"),
        accessor="opportunity_access__user__phone_number",
        default="—",
    )
    task_type = tables.Column(verbose_name=gettext_lazy("Task Type"), accessor="task_type__name")
    action = tables.TemplateColumn(
        verbose_name="",
        orderable=False,
        template_name="opportunity/assigned_task_edit_button.html",
    )

    def __init__(self, *args, can_edit_tasks=False, can_delete_tasks=False, **kwargs):
        super().__init__(*args, **kwargs)
        if not can_edit_tasks:
            self.columns.hide("action")
        if not can_delete_tasks:
            self.columns.hide("select")

    class Meta:
        model = AssignedTask
        fields = ()
        sequence = (
            "select",
            "connect_worker",
            "phone_number",
            "status",
            "task_type",
            "assigned_date",
            "due_date",
            "assigned_by",
            "action",
        )
        order_by = ("-assigned_date",)
        row_attrs = {"class": "group"}
        empty_text = gettext_lazy("No tasks have been assigned yet.")

    def render_connect_worker(self, value):
        return format_html(
            '<div class="flex flex-col items-start">'
            '<p class="text-sm font-medium text-gray-700">{}</p>'
            '<p class="text-xs text-gray-500">{}</p>'
            "</div>",
            value.name,
            value.username,
        )


class TaskTable(OpportunityContextTable):
    index = IndexColumn()
    name = tables.Column(verbose_name=gettext_lazy("Task Type Name"))
    description = tables.Column(verbose_name=gettext_lazy("Description"))
    mode = tables.Column(verbose_name=gettext_lazy("Mode"))
    linked_task_unit = tables.Column(verbose_name=gettext_lazy("Linked Task Unit"), accessor="unit_name")
    archived = tables.DateColumn(verbose_name=gettext_lazy("Archived"))
    actions = tables.TemplateColumn(
        verbose_name=gettext_lazy("Actions"),
        orderable=False,
        template_code="""
            {% load i18n %}
            <button
                type="button"
                class="button button-md outline-style"
                hx-get="{% url 'opportunity:edit_task_type' table.org_slug table.opp_id record.pk %}"
                hx-target="#edit-task-form"
                @htmx:after-request="if ($event.detail.successful) {
                        showEditTaskTypeModal = true; editTaskError = false
                    } else { editTaskError = true }">
                <i class="fa-regular fa-pen-to-square mr-1"></i> {% translate "Edit" %}
            </button>
        """,
    )

    class Meta:
        model = TaskType
        fields = ("index", "name", "description", "mode", "linked_task_unit", "archived", "actions")
        empty_text = gettext_lazy("No task types configured for this opportunity.")

    def render_mode(self, value, record):
        if value == TaskTypeModeChoices.OCS:
            badge_class = "badge badge-sm bg-indigo-600/20 text-indigo-600"
        else:
            badge_class = "badge badge-sm bg-slate-100 text-slate-500"
        return format_html('<span class="{}">{}</span>', badge_class, record.get_mode_display())
