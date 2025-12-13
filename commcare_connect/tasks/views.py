import json
import logging
from datetime import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, DetailView, ListView, TemplateView, UpdateView

from commcare_connect.audit.management.extractors.connect_api_facade import ConnectAPIFacade
from commcare_connect.tasks.forms import TaskCommentForm, TaskCreateForm, TaskQuickUpdateForm, TaskUpdateForm
from commcare_connect.tasks.helpers import get_user_tasks_queryset, user_can_access_task
from commcare_connect.tasks.models import Task, TaskAISession, TaskEvent, TaskEventType
from commcare_connect.tasks.ocs_client import OCSClientError, get_recent_session, get_transcript, trigger_bot

logger = logging.getLogger(__name__)


class TaskAccessMixin(UserPassesTestMixin):
    """Mixin to check if user can access a task."""

    def test_func(self):
        task = self.get_object()
        return user_can_access_task(self.request.user, task)


class TaskListView(LoginRequiredMixin, ListView):
    """List view for tasks with filtering and statistics."""

    model = Task
    template_name = "tasks/tasks_list.html"
    context_object_name = "tasks"
    paginate_by = 50

    def get_queryset(self):
        """Get tasks the user can access with filtering."""
        queryset = get_user_tasks_queryset(self.request.user)

        # Apply filters from GET parameters
        status_filter = self.request.GET.get("status")
        if status_filter and status_filter != "all":
            queryset = queryset.filter(status=status_filter)

        action_type_filter = self.request.GET.get("action_type")
        if action_type_filter and action_type_filter != "all":
            queryset = queryset.filter(task_type=action_type_filter)

        search_query = self.request.GET.get("search")
        if search_query:
            queryset = queryset.filter(
                Q(user__name__icontains=search_query)
                | Q(user__email__icontains=search_query)
                | Q(title__icontains=search_query)
                | Q(description__icontains=search_query)
            )

        # Optimize queries
        queryset = queryset.select_related("user", "opportunity", "assigned_to")

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Get all tasks for stats (before filtering)
        all_tasks = get_user_tasks_queryset(self.request.user)

        # Calculate statistics
        stats = {
            "total": all_tasks.count(),
            "unassigned": all_tasks.filter(status="unassigned").count(),
            "network_manager": all_tasks.filter(status="network_manager").count(),
            "program_manager": all_tasks.filter(status="program_manager").count(),
            "action_underway": all_tasks.filter(status="action_underway").count(),
            "resolved": all_tasks.filter(status="resolved").count(),
        }

        # Get unique values for filter dropdowns
        statuses = Task._meta.get_field("status").choices
        action_types = Task._meta.get_field("task_type").choices

        # Check for Connect OAuth token (for task creation wizard)
        from allauth.socialaccount.models import SocialAccount, SocialToken

        try:
            social_account = SocialAccount.objects.get(user=self.request.user, provider="connect")
            social_token = SocialToken.objects.get(account=social_account)
            context["has_connect_token"] = True
            context["token_expires_at"] = social_token.expires_at
        except (SocialAccount.DoesNotExist, SocialToken.DoesNotExist):
            context["has_connect_token"] = False
            context["token_expires_at"] = None

        context.update(
            {
                "stats": stats,
                "statuses": [choice[0] for choice in statuses],
                "action_types": [choice[0] for choice in action_types],
                "selected_status": self.request.GET.get("status", "all"),
                "selected_action_type": self.request.GET.get("action_type", "all"),
            }
        )

        return context


class TaskDetailView(LoginRequiredMixin, TaskAccessMixin, DetailView):
    """Detail view for a single task."""

    model = Task
    template_name = "tasks/task_detail_streamlined.html"
    context_object_name = "task"
    pk_url_kwarg = "task_id"

    def get_queryset(self):
        """Optimize query with related objects."""
        return (
            Task.objects.select_related("user", "opportunity", "assigned_to", "created_by_user")
            .prefetch_related("events", "comments__author", "ai_sessions")
            .all()
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        task = self.object

        # Prepare timeline data (events and comments combined)
        timeline = []

        # Add events to timeline
        for event in task.events.all():
            timeline_item = {
                "timestamp": event.date_created,
                "actor": event.actor,
                "action": event.get_event_type_display(),
                "description": event.description,
                "icon": self._get_event_icon(event.event_type),
                "color": self._get_event_color(event.event_type),
                "display_name": event.actor,
                "type": "event",
            }

            # Add role badge if actor is PM or NM
            if event.actor_user:
                if hasattr(event.actor_user, "memberships"):
                    memberships = event.actor_user.memberships.all()
                    for membership in memberships:
                        if membership.is_admin:
                            if membership.is_program_manager:
                                timeline_item["actor_role"] = "program_manager"
                                break
                            else:
                                timeline_item["actor_role"] = "network_manager"
                                break

            # If this is an AI conversation event, include session info
            if event.event_type == TaskEventType.AI_CONVERSATION:
                # Use the explicit relationship to get the session
                if event.ai_session:
                    timeline_item["session_id"] = event.ai_session.ocs_session_id
                    timeline_item["session_status"] = event.ai_session.status

            timeline.append(timeline_item)

        # Add comments to timeline
        for comment in task.comments.all():
            comment_item = {
                "timestamp": comment.date_created,
                "actor": comment.author.name,
                "action": "Commented",
                "description": comment.content,
                "icon": "fa-comment",
                "color": "gray",
                "display_name": comment.author.name,
                "type": "comment",
            }

            # Add role badge if commenter is PM or NM
            if hasattr(comment.author, "memberships"):
                memberships = comment.author.memberships.all()
                for membership in memberships:
                    if membership.is_admin:
                        if membership.is_program_manager:
                            comment_item["actor_role"] = "program_manager"
                            break
                        else:
                            comment_item["actor_role"] = "network_manager"
                            break

            timeline.append(comment_item)

        # Sort timeline by timestamp (newest first)
        timeline.sort(key=lambda x: x["timestamp"], reverse=True)

        # Get FLW history (past tasks for the same user)
        flw_history = (
            Task.objects.filter(user=task.user)
            .exclude(id=task.id)
            .order_by("-date_created")[:5]
            .values("id", "date_created", "task_type", "status", "title")
        )

        context.update(
            {
                "task": {
                    **task.__dict__,
                    "flw_name": task.user.name,
                    "flw_username": task.user.username,
                    "opportunity": task.opportunity.name,
                    "action_type": task.task_type,
                    "created": task.date_created,
                    "timeline": timeline,
                    "flw_history": list(flw_history),
                },
                "comment_form": TaskCommentForm(),
                "update_form": TaskUpdateForm(instance=task),
            }
        )

        return context

    def _get_event_icon(self, event_type):
        """Get FontAwesome icon for event type."""
        icon_map = {
            "created": "fa-plus-circle",
            "status_changed": "fa-exchange-alt",
            "assigned": "fa-user-check",
            "commented": "fa-comment",
            "learning_assigned": "fa-graduation-cap",
            "learning_completed": "fa-check-circle",
            "ai_conversation": "fa-robot",
            "notification_sent": "fa-envelope",
            "flw_acknowledged": "fa-check",
            "pattern_detected": "fa-exclamation-circle",
        }
        return icon_map.get(event_type, "fa-circle")

    def _get_event_color(self, event_type):
        """Get color for event type."""
        color_map = {
            "created": "blue",
            "status_changed": "purple",
            "assigned": "indigo",
            "commented": "gray",
            "learning_assigned": "blue",
            "learning_completed": "green",
            "ai_conversation": "green",
            "notification_sent": "green",
            "flw_acknowledged": "green",
            "pattern_detected": "red",
        }
        return color_map.get(event_type, "gray")


class TaskCreateView(LoginRequiredMixin, CreateView):
    """Create a new task."""

    model = Task
    form_class = TaskCreateForm
    template_name = "tasks/task_form.html"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def form_valid(self, form):
        # Set created_by_user
        form.instance.created_by_user = self.request.user
        form.instance.created_by = self.request.user.email
        form.instance.modified_by = self.request.user.email

        response = super().form_valid(form)

        # Create initial event
        TaskEvent.objects.create(
            task=self.object,
            event_type=TaskEventType.CREATED,
            actor=self.request.user.name,
            actor_user=self.request.user,
            description=f"Task created by {self.request.user.name}",
            created_by=self.request.user.email,
            modified_by=self.request.user.email,
        )

        messages.success(self.request, f"Task #{self.object.id} created successfully.")
        return response

    def get_success_url(self):
        return reverse("tasks:detail", kwargs={"task_id": self.object.id})


class TaskUpdateView(LoginRequiredMixin, TaskAccessMixin, UpdateView):
    """Update an existing task."""

    model = Task
    form_class = TaskUpdateForm
    template_name = "tasks/task_form.html"
    pk_url_kwarg = "task_id"

    def form_valid(self, form):
        # Track what changed
        changes = []
        old_instance = Task.objects.get(pk=self.object.pk)

        if old_instance.status != form.instance.status:
            changes.append(
                f"Status changed from {old_instance.get_status_display()} to {form.instance.get_status_display()}"
            )

        if old_instance.priority != form.instance.priority:
            old_priority = old_instance.get_priority_display()
            new_priority = form.instance.get_priority_display()
            changes.append(f"Priority changed from {old_priority} to {new_priority}")

        if old_instance.assigned_to != form.instance.assigned_to:
            old_name = old_instance.assigned_to.name if old_instance.assigned_to else "Unassigned"
            new_name = form.instance.assigned_to.name if form.instance.assigned_to else "Unassigned"
            changes.append(f"Assigned to changed from {old_name} to {new_name}")

        form.instance.modified_by = self.request.user.email
        response = super().form_valid(form)

        # Create events for changes
        for change in changes:
            TaskEvent.objects.create(
                task=self.object,
                event_type=TaskEventType.STATUS_CHANGED if "Status" in change else TaskEventType.ASSIGNED,
                actor=self.request.user.name,
                actor_user=self.request.user,
                description=change,
                created_by=self.request.user.email,
                modified_by=self.request.user.email,
            )

        messages.success(self.request, "Task updated successfully.")
        return response

    def get_success_url(self):
        return reverse("tasks:detail", kwargs={"task_id": self.object.id})


@login_required
@require_POST
def task_add_comment(request, task_id):
    """Add a comment to a task."""
    task = get_object_or_404(Task, id=task_id)

    if not user_can_access_task(request.user, task):
        return JsonResponse({"error": "Access denied"}, status=403)

    form = TaskCommentForm(request.POST)
    if form.is_valid():
        comment = form.save(commit=False)
        comment.task = task
        comment.author = request.user
        comment.created_by = request.user.email
        comment.modified_by = request.user.email
        comment.save()

        # Create event
        TaskEvent.objects.create(
            task=task,
            event_type=TaskEventType.COMMENTED,
            actor=request.user.name,
            actor_user=request.user,
            description=comment.content,
            created_by=request.user.email,
            modified_by=request.user.email,
        )

        # Return JSON for AJAX
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse(
                {
                    "success": True,
                    "comment": {
                        "id": comment.id,
                        "author": comment.author.name,
                        "content": comment.content,
                        "date": comment.date_created.isoformat(),
                    },
                }
            )

        messages.success(request, "Comment added successfully.")
        return redirect("tasks:detail", task_id=task_id)

    return JsonResponse({"error": "Invalid form data"}, status=400)


@login_required
@csrf_exempt
@require_POST
def task_initiate_ai(request, task_id):
    """Initiate an AI assistant conversation for a task."""
    task = get_object_or_404(Task, id=task_id)

    if not user_can_access_task(request.user, task):
        return JsonResponse({"error": "Access denied"}, status=403)

    try:
        # Parse request body
        body = json.loads(request.body)

        # Extract parameters from request
        identifier = body.get("identifier", "").strip()
        experiment = body.get("experiment", "").strip()
        platform = body.get("platform", "commcare_connect")
        prompt_text = body.get("prompt_text", "").strip()
        start_new_session = body.get("start_new_session", False)

        # Validate required fields
        if not identifier:
            return JsonResponse({"error": "Participant ID is required"}, status=400)
        if not experiment:
            return JsonResponse({"error": "Bot ID (experiment) is required"}, status=400)
        if not prompt_text:
            return JsonResponse({"error": "Prompt instructions are required"}, status=400)

        # Prepare session data to link back to Connect
        session_data = {
            "task_id": str(task.id),
            "task_type": task.task_type,
            "opportunity_id": str(task.opportunity.id),
            "opportunity_name": task.opportunity.name,
            "flw_user_id": str(task.user.id),
            "flw_name": task.user.name,
            "created_by": request.user.email,
        }

        # Trigger bot with OCS using manual parameters
        trigger_bot(
            identifier=identifier,
            platform=platform,
            bot_id=experiment,
            prompt_text=prompt_text,
            start_new_session=start_new_session,
            session_data=session_data,
        )

        # Create AI session record
        # Note: OCS doesn't return session_id in the trigger response
        # Session ID will remain null and can be populated manually later
        ai_session = TaskAISession.objects.create(
            task=task,
            ocs_session_id=None,  # Leave null - populate manually later
            status="pending",
            session_metadata={
                "parameters": {
                    "identifier": identifier,
                    "experiment": experiment,
                    "platform": platform,
                    "prompt_text": prompt_text,
                    "start_new_session": start_new_session,
                },
                "session_data": session_data,
            },
            created_by=request.user.email,
            modified_by=request.user.email,
        )

        # Create event linked to the AI session
        TaskEvent.objects.create(
            task=task,
            event_type=TaskEventType.AI_CONVERSATION,
            actor=request.user.name,
            actor_user=request.user,
            ai_session=ai_session,  # Link the event to this specific AI session
            description=f"AI assistant conversation initiated (Platform: {platform}, Bot: {experiment})",
            created_by=request.user.email,
            modified_by=request.user.email,
            metadata={
                "platform": platform,
                "bot_id": experiment,
                "identifier": identifier,
            },
        )

        message = "AI conversation initiated successfully."
        return JsonResponse(
            {
                "success": True,
                "message": message,
                "session_id": None,
                "task_ai_session_id": ai_session.id,  # Return the TaskAISession ID for polling
            }
        )

    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON in request body"}, status=400)
    except OCSClientError as e:
        return JsonResponse({"error": f"OCS API error: {str(e)}"}, status=500)
    except Exception as e:
        return JsonResponse({"error": f"Unexpected error: {str(e)}"}, status=500)


@login_required
def task_ai_sessions(request, task_id):
    """Get AI sessions for a task and try to populate session_id from OCS if missing."""
    task = get_object_or_404(Task, id=task_id)

    if not user_can_access_task(request.user, task):
        return JsonResponse({"error": "Access denied"}, status=403)

    # Get the specific session ID from query params (if polling for a specific session)
    task_ai_session_id = request.GET.get("session_id")

    pending_session = None
    if task_ai_session_id:
        # Get the specific session we're polling for
        try:
            pending_session = TaskAISession.objects.get(id=task_ai_session_id, task=task, ocs_session_id__isnull=True)
        except TaskAISession.DoesNotExist:
            pass

    # Try to fetch and populate session_id from OCS
    if pending_session and pending_session.session_metadata:
        params = pending_session.session_metadata.get("parameters", {})
        experiment = params.get("experiment")
        identifier = params.get("identifier")

        if experiment and identifier:
            try:
                # Query OCS for recent sessions
                sessions = get_recent_session(experiment_id=experiment, identifier=identifier, limit=5)

                if sessions:
                    # Get the most recent session created after this TaskAISession was created
                    for session in sessions:
                        session_created_str = session.get("created_at", "")
                        if session_created_str:
                            session_created = datetime.fromisoformat(session_created_str.replace("Z", "+00:00"))
                            # Check if session was created after we triggered (within 30 seconds tolerance)
                            time_diff = (session_created - pending_session.date_created).total_seconds()
                            if -5 <= time_diff <= 30:  # Allow 5 second tolerance for clock differences
                                session_id = session.get("id")
                                if session_id:
                                    # Update the TaskAISession
                                    pending_session.ocs_session_id = session_id
                                    pending_session.status = "initiated"
                                    pending_session.save(update_fields=["ocs_session_id", "status"])
                                    logger.info(
                                        f"Populated session ID {session_id} for TaskAISession {pending_session.id}"
                                    )
                                    break
            except Exception as e:
                logger.warning(f"Could not fetch session from OCS: {e}")

    # Get all AI sessions for this task, ordered by most recent first
    sessions = (
        TaskAISession.objects.filter(task=task)
        .order_by("-date_created")
        .values("id", "ocs_session_id", "status", "date_created", "session_metadata")
    )

    return JsonResponse({"success": True, "sessions": list(sessions)})


@login_required
@csrf_exempt
@require_POST
def task_add_ai_session(request, task_id):
    """Manually add OCS session ID to a task."""
    task = get_object_or_404(Task, id=task_id)

    if not user_can_access_task(request.user, task):
        return JsonResponse({"error": "Access denied"}, status=403)

    session_id = request.POST.get("session_id", "").strip()

    if not session_id:
        return JsonResponse({"success": False, "error": "Session ID is required"}, status=400)

    # Create or update AI session
    ai_session, created = TaskAISession.objects.update_or_create(
        task=task,
        defaults={
            "ocs_session_id": session_id,
            "status": "completed",
            "created_by": request.user.email,
            "modified_by": request.user.email,
        },
    )

    # Update existing AI conversation event with session ID, or create new one
    ai_event = task.events.filter(event_type=TaskEventType.AI_CONVERSATION).first()
    if ai_event:
        # Update existing event with session ID info
        ai_event.description = f"{ai_event.description} (Session: {session_id})"
        ai_event.modified_by = request.user.email
        ai_event.save(update_fields=["description", "modified_by", "date_modified"])
    else:
        # Create new event if none exists
        TaskEvent.objects.create(
            task=task,
            event_type=TaskEventType.AI_CONVERSATION,
            actor=request.user.name,
            description=f"AI assistant session linked: {session_id}",
            created_by=request.user.email,
            modified_by=request.user.email,
        )

    return JsonResponse({"success": True, "session_id": session_id, "created": created})


@login_required
def task_ai_transcript(request, task_id):
    """Fetch AI conversation transcript from OCS."""
    task = get_object_or_404(Task, id=task_id)

    if not user_can_access_task(request.user, task):
        return JsonResponse({"error": "Access denied"}, status=403)

    # Get AI session for this task
    ai_session = task.ai_sessions.first()

    if not ai_session:
        return JsonResponse({"success": False, "error": "No AI session found for this task"}, status=404)

    if not ai_session.ocs_session_id:
        return JsonResponse({"success": False, "error": "No session ID available yet"}, status=404)

    # Always fetch fresh from OCS to get latest messages
    try:
        transcript = get_transcript(ai_session.ocs_session_id)

        # Transform OCS format to UI format
        if isinstance(transcript, dict) and transcript.get("messages"):
            messages = []
            for msg in transcript["messages"]:
                messages.append(
                    {
                        "actor": "AI Assistant" if msg.get("role") == "assistant" else task.user.name,
                        "message": msg.get("content", ""),
                        "timestamp": msg.get("created_at", ""),
                    }
                )

            return JsonResponse({"success": True, "session_id": ai_session.ocs_session_id, "messages": messages})
        else:
            return JsonResponse(
                {
                    "success": True,
                    "session_id": ai_session.ocs_session_id,
                    "transcript": transcript,
                }
            )

    except OCSClientError as e:
        return JsonResponse({"success": False, "error": str(e)}, status=500)


@login_required
@require_POST
def task_quick_update(request, task_id):
    """Quick update for status/assignment via AJAX."""
    task = get_object_or_404(Task, id=task_id)

    if not user_can_access_task(request.user, task):
        return JsonResponse({"error": "Access denied"}, status=403)

    form = TaskQuickUpdateForm(request.POST)
    if form.is_valid():
        changes = []

        if form.cleaned_data.get("status"):
            old_status = task.status
            task.status = form.cleaned_data["status"]
            if old_status != task.status:
                changes.append(f"Status changed to {task.get_status_display()}")

        if form.cleaned_data.get("priority"):
            old_priority = task.priority
            task.priority = form.cleaned_data["priority"]
            if old_priority != task.priority:
                changes.append(f"Priority changed to {task.get_priority_display()}")

        if form.cleaned_data.get("assigned_to"):
            old_assigned = task.assigned_to
            task.assigned_to = form.cleaned_data["assigned_to"]
            if old_assigned != task.assigned_to:
                changes.append(f"Assigned to {task.assigned_to.name}")

        task.modified_by = request.user.email
        task.save()

        # Create events
        for change in changes:
            TaskEvent.objects.create(
                task=task,
                event_type=TaskEventType.STATUS_CHANGED,
                actor=request.user.name,
                actor_user=request.user,
                description=change,
                created_by=request.user.email,
                modified_by=request.user.email,
            )

        return JsonResponse({"success": True, "changes": changes})

    return JsonResponse({"error": "Invalid form data"}, status=400)


# Database management API views
class DatabaseStatsAPIView(LoginRequiredMixin, View):
    """API endpoint for getting database statistics."""

    def get(self, request):
        from commcare_connect.tasks.database_manager import get_database_stats

        try:
            stats = get_database_stats()
            return JsonResponse({"success": True, "stats": stats})
        except Exception as e:
            return JsonResponse({"error": str(e)}, status=500)


@method_decorator(csrf_exempt, name="dispatch")
class DatabaseResetAPIView(LoginRequiredMixin, View):
    """API endpoint for resetting tasks-related database tables."""

    def post(self, request):
        from commcare_connect.tasks.database_manager import reset_tasks_database

        try:
            deleted = reset_tasks_database()
            return JsonResponse({"success": True, "deleted": deleted})
        except Exception as e:
            import traceback

            return JsonResponse({"error": str(e), "traceback": traceback.format_exc()}, status=500)


# Task Creation Wizard with OAuth support


class TaskCreationWizardView(LoginRequiredMixin, TemplateView):
    """Wizard interface for creating tasks using Connect OAuth."""

    template_name = "tasks/task_creation_wizard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Check for Connect OAuth token
        from allauth.socialaccount.models import SocialAccount, SocialToken

        try:
            social_account = SocialAccount.objects.get(user=self.request.user, provider="connect")
            social_token = SocialToken.objects.get(account=social_account)
            context["has_connect_token"] = True
            context["token_expires_at"] = social_token.expires_at
        except (SocialAccount.DoesNotExist, SocialToken.DoesNotExist):
            context["has_connect_token"] = False
            context["token_expires_at"] = None

        return context


# Task Creation API Endpoints


class OpportunitySearchAPIView(LoginRequiredMixin, View):
    """API endpoint for searching opportunities via ConnectAPIFacade."""

    def get(self, request):
        query = request.GET.get("query", "")
        limit = int(request.GET.get("limit", 50))

        try:
            # Initialize facade with user for OAuth
            facade = ConnectAPIFacade(user=request.user)

            if not facade.authenticate():
                return JsonResponse({"error": "Failed to authenticate with data source"}, status=500)

            # Search opportunities
            opportunities = facade.search_opportunities(query, limit)

            # Convert to JSON-serializable format
            results = []
            for opp in opportunities:
                results.append(
                    {
                        "id": opp.id,
                        "name": opp.name,
                        "description": opp.description,
                        "organization_name": opp.organization_name,
                        "program_name": opp.program_name,
                        "start_date": opp.start_date.isoformat() if opp.start_date else None,
                        "end_date": opp.end_date.isoformat() if opp.end_date else None,
                        "active": opp.active,
                        "is_test": opp.is_test,
                        "visit_count": opp.visit_count,
                    }
                )

            facade.close()
            return JsonResponse({"success": True, "opportunities": results})

        except Exception as e:
            return JsonResponse({"error": str(e)}, status=500)


class OpportunityFieldWorkersAPIView(LoginRequiredMixin, View):
    """API endpoint for getting field workers for an opportunity."""

    def get(self, request, opportunity_id):
        try:
            # Initialize facade with user for OAuth
            facade = ConnectAPIFacade(user=request.user)

            if not facade.authenticate():
                return JsonResponse({"error": "Failed to authenticate with data source"}, status=500)

            # Get field workers
            field_workers = facade.get_field_workers_by_opportunity(opportunity_id)

            # Convert to JSON-serializable format
            results = []
            for flw in field_workers:
                results.append(
                    {
                        "id": flw.id,
                        "name": flw.name,
                        "email": flw.email,
                        "phone_number": flw.phone_number,
                        "username": flw.username,
                        "last_active": flw.last_active.isoformat() if flw.last_active else None,
                        "total_visits": flw.total_visits,
                        "approved_visits": flw.approved_visits,
                        "pending_visits": flw.pending_visits,
                        "rejected_visits": flw.rejected_visits,
                    }
                )

            facade.close()
            return JsonResponse({"success": True, "field_workers": results})

        except Exception as e:
            return JsonResponse({"error": str(e)}, status=500)


@method_decorator(csrf_exempt, name="dispatch")
class BulkTaskCreateAPIView(LoginRequiredMixin, View):
    """API endpoint for bulk task creation."""

    def post(self, request):
        try:
            data = json.loads(request.body)

            # Extract parameters
            opportunity_id = data.get("opportunity_id")
            flw_ids = data.get("flw_ids", [])
            task_type = data.get("task_type", "warning")
            priority = data.get("priority", "medium")
            title = data.get("title", "")
            description = data.get("description", "")
            learning_assignment_text = data.get("learning_assignment_text", "")
            assigned_to_id = data.get("assigned_to_id")

            # Validate required fields
            if not opportunity_id:
                return JsonResponse({"error": "Opportunity ID is required"}, status=400)

            if not flw_ids:
                return JsonResponse({"error": "At least one FLW must be selected"}, status=400)

            if not title:
                return JsonResponse({"error": "Title is required"}, status=400)

            # Sync opportunity and users from Connect API if needed
            from commcare_connect.audit.management.extractors.connect_api_facade import ConnectAPIFacade
            from commcare_connect.connect_sync.services import ConnectDataSyncService
            from commcare_connect.opportunity.models import Opportunity
            from commcare_connect.users.models import User

            # Initialize facade and sync service
            facade = ConnectAPIFacade(user=request.user)
            if not facade.authenticate():
                return JsonResponse({"error": "Failed to authenticate with Connect API"}, status=500)

            sync_service = ConnectDataSyncService(facade)

            # Sync opportunity if not found locally
            try:
                opportunity = Opportunity.objects.filter(id=opportunity_id).first()
                if not opportunity:
                    print(f"[INFO] Syncing opportunity {opportunity_id} from Connect API...")
                    opportunity = sync_service.sync_opportunity(opportunity_id)
            except ValueError as e:
                facade.close()
                return JsonResponse({"error": str(e)}, status=404)

            # Sync users if not found locally (collect all usernames first)
            usernames_to_sync = []
            for flw_identifier in flw_ids:
                # Skip numeric IDs (already exist locally)
                if not (
                    isinstance(flw_identifier, int) or (isinstance(flw_identifier, str) and flw_identifier.isdigit())
                ):
                    if not User.objects.filter(username=flw_identifier).exists():
                        usernames_to_sync.append(flw_identifier)

            # Batch sync all missing users
            if usernames_to_sync:
                print(f"[INFO] Syncing {len(usernames_to_sync)} users from Connect API...")
                sync_service.sync_users_by_username(usernames_to_sync, opportunity_id)

            facade.close()

            # Now create tasks
            created_tasks = []
            errors = []

            for flw_identifier in flw_ids:
                try:
                    # Get user (by ID or username)
                    try:
                        if isinstance(flw_identifier, int) or (
                            isinstance(flw_identifier, str) and flw_identifier.isdigit()
                        ):
                            user = User.objects.get(id=int(flw_identifier))
                        else:
                            user = User.objects.get(username=flw_identifier)
                    except User.DoesNotExist:
                        errors.append(f"User {flw_identifier} not found after sync attempt")
                        continue

                    # Get assigned_to user if specified
                    assigned_to = None
                    if assigned_to_id:
                        try:
                            assigned_to = User.objects.get(id=assigned_to_id)
                        except User.DoesNotExist:
                            pass

                    # Create task
                    task = Task.objects.create(
                        user=user,
                        opportunity=opportunity,
                        task_type=task_type,
                        priority=priority,
                        title=title,
                        description=description,
                        learning_assignment_text=learning_assignment_text,
                        assigned_to=assigned_to,
                        created_by_user=request.user,
                        created_by=request.user.email,
                        modified_by=request.user.email,
                    )

                    # Create initial event
                    TaskEvent.objects.create(
                        task=task,
                        event_type=TaskEventType.CREATED,
                        actor=request.user.name,
                        actor_user=request.user,
                        description=f"Task created by {request.user.name} (bulk creation)",
                        created_by=request.user.email,
                        modified_by=request.user.email,
                    )

                    created_tasks.append({"id": task.id, "user_name": user.name, "user_id": user.id})

                except Exception as e:
                    errors.append(f"Error creating task for user {flw_identifier}: {str(e)}")

            return JsonResponse(
                {"success": True, "created_count": len(created_tasks), "tasks": created_tasks, "errors": errors}
            )

        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON in request body"}, status=400)
        except Exception as e:
            return JsonResponse({"error": str(e)}, status=500)


# Keep the old view names for backward compatibility during transition
tasks_list = TaskListView.as_view()
task_detail_streamlined = TaskDetailView.as_view()
