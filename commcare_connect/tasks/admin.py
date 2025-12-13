from django.contrib import admin

from commcare_connect.tasks.models import OpportunityBotConfiguration, Task, TaskAISession, TaskComment, TaskEvent


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "user",
        "opportunity",
        "task_type",
        "status",
        "priority",
        "assigned_to",
        "date_created",
    ]
    list_filter = [
        "task_type",
        "status",
        "priority",
        "date_created",
    ]
    search_fields = [
        "user__name",
        "user__email",
        "opportunity__name",
        "title",
        "description",
    ]
    readonly_fields = [
        "date_created",
        "date_modified",
        "created_by",
        "modified_by",
    ]
    raw_id_fields = [
        "user",
        "opportunity",
        "created_by_user",
        "assigned_to",
    ]


@admin.register(TaskEvent)
class TaskEventAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "task",
        "event_type",
        "actor",
        "date_created",
    ]
    list_filter = [
        "event_type",
        "date_created",
    ]
    search_fields = [
        "task__id",
        "actor",
        "description",
    ]
    readonly_fields = [
        "date_created",
        "date_modified",
        "created_by",
        "modified_by",
    ]
    raw_id_fields = [
        "task",
        "actor_user",
    ]


@admin.register(TaskComment)
class TaskCommentAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "task",
        "author",
        "date_created",
    ]
    list_filter = [
        "date_created",
    ]
    search_fields = [
        "task__id",
        "author__name",
        "content",
    ]
    readonly_fields = [
        "date_created",
        "date_modified",
        "created_by",
        "modified_by",
    ]
    raw_id_fields = [
        "task",
        "author",
    ]


@admin.register(TaskAISession)
class TaskAISessionAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "task",
        "ocs_session_id",
        "status",
        "date_created",
    ]
    list_filter = [
        "status",
        "date_created",
    ]
    search_fields = [
        "task__id",
        "ocs_session_id",
    ]
    readonly_fields = [
        "date_created",
        "date_modified",
        "created_by",
        "modified_by",
    ]
    raw_id_fields = [
        "task",
    ]


@admin.register(OpportunityBotConfiguration)
class OpportunityBotConfigurationAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "opportunity",
        "ocs_bot_id",
        "bot_name",
        "is_active",
        "date_created",
    ]
    list_filter = [
        "is_active",
        "date_created",
    ]
    search_fields = [
        "opportunity__name",
        "ocs_bot_id",
        "bot_name",
    ]
    readonly_fields = [
        "date_created",
        "date_modified",
        "created_by",
        "modified_by",
    ]
    raw_id_fields = [
        "opportunity",
    ]
