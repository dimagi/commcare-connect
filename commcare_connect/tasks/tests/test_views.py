import pytest
from django.test import Client
from django.urls import reverse

from commcare_connect.opportunity.tests.factories import OpportunityAccessFactory, OpportunityFactory
from commcare_connect.tasks.models import Task, TaskComment, TaskEvent
from commcare_connect.tasks.tests.factories import TaskFactory
from commcare_connect.users.tests.factories import UserFactory


@pytest.fixture
def client():
    return Client()


@pytest.fixture
def user():
    return UserFactory()


@pytest.fixture
def task_with_access(user):
    """Create a task that the user has access to."""
    opportunity = OpportunityFactory()
    OpportunityAccessFactory(user=user, opportunity=opportunity)
    return TaskFactory(opportunity=opportunity, user=user)


@pytest.mark.django_db
class TestTaskListView:
    def test_task_list_requires_login(self, client):
        """Test task list requires authentication."""
        url = reverse("tasks:list")
        response = client.get(url)
        assert response.status_code == 302
        assert "/accounts/login/" in response.url

    def test_task_list_shows_accessible_tasks(self, client, user, task_with_access):
        """Test task list shows tasks user can access."""
        client.force_login(user)
        url = reverse("tasks:list")
        response = client.get(url)

        assert response.status_code == 200
        assert task_with_access in response.context["tasks"]

    def test_task_list_filters_by_status(self, client, user, task_with_access):
        """Test task list can filter by status."""
        task_with_access.status = "resolved"
        task_with_access.save()

        client.force_login(user)
        url = reverse("tasks:list") + "?status=resolved"
        response = client.get(url)

        assert response.status_code == 200
        assert task_with_access in response.context["tasks"]

    def test_task_list_statistics(self, client, user, task_with_access):
        """Test task list includes statistics."""
        client.force_login(user)
        url = reverse("tasks:list")
        response = client.get(url)

        assert response.status_code == 200
        assert "stats" in response.context
        assert response.context["stats"]["total"] >= 1


@pytest.mark.django_db
class TestTaskDetailView:
    def test_task_detail_requires_login(self, client, task_with_access):
        """Test task detail requires authentication."""
        url = reverse("tasks:detail", kwargs={"task_id": task_with_access.id})
        response = client.get(url)
        assert response.status_code == 302

    def test_task_detail_accessible_with_permission(self, client, user, task_with_access):
        """Test task detail is accessible with proper permissions."""
        client.force_login(user)
        url = reverse("tasks:detail", kwargs={"task_id": task_with_access.id})
        response = client.get(url)

        assert response.status_code == 200
        assert response.context["task"]["flw_name"] == task_with_access.user.name

    def test_task_detail_denied_without_permission(self, client):
        """Test task detail is denied without permission."""
        unauthorized_user = UserFactory()
        task = TaskFactory()

        client.force_login(unauthorized_user)
        url = reverse("tasks:detail", kwargs={"task_id": task.id})
        response = client.get(url)

        assert response.status_code == 403


@pytest.mark.django_db
class TestTaskCreateView:
    def test_task_create_requires_login(self, client):
        """Test task creation requires authentication."""
        url = reverse("tasks:create")
        response = client.get(url)
        assert response.status_code == 302

    def test_task_create_form_displayed(self, client, user):
        """Test create form is displayed."""
        client.force_login(user)
        url = reverse("tasks:create")
        response = client.get(url)

        assert response.status_code == 200
        assert "form" in response.context

    def test_task_create_success(self, client, user):
        """Test successful task creation."""
        opportunity = OpportunityFactory()
        OpportunityAccessFactory(user=user, opportunity=opportunity)
        flw_user = UserFactory()
        OpportunityAccessFactory(user=flw_user, opportunity=opportunity)

        client.force_login(user)
        url = reverse("tasks:create")

        data = {
            "user": flw_user.id,
            "opportunity": opportunity.id,
            "task_type": "warning",
            "priority": "high",
            "title": "Test Task",
            "description": "Test description",
            "learning_assignment_text": "",
        }

        response = client.post(url, data)

        assert response.status_code == 302
        assert Task.objects.filter(title="Test Task").exists()

        task = Task.objects.get(title="Test Task")
        assert task.created_by_user == user
        assert TaskEvent.objects.filter(task=task, event_type="created").exists()


@pytest.mark.django_db
class TestTaskUpdateView:
    def test_task_update_requires_login(self, client, task_with_access):
        """Test task update requires authentication."""
        url = reverse("tasks:update", kwargs={"task_id": task_with_access.id})
        response = client.get(url)
        assert response.status_code == 302

    def test_task_update_form_displayed(self, client, user, task_with_access):
        """Test update form is displayed."""
        client.force_login(user)
        url = reverse("tasks:update", kwargs={"task_id": task_with_access.id})
        response = client.get(url)

        assert response.status_code == 200
        assert "form" in response.context

    def test_task_update_success(self, client, user, task_with_access):
        """Test successful task update."""
        client.force_login(user)
        url = reverse("tasks:update", kwargs={"task_id": task_with_access.id})

        data = {
            "status": "resolved",
            "priority": "low",
            "learning_assignment_text": "New learning text",
        }

        response = client.post(url, data)

        assert response.status_code == 302

        task_with_access.refresh_from_db()
        assert task_with_access.status == "resolved"
        assert task_with_access.priority == "low"


@pytest.mark.django_db
class TestTaskCommentView:
    def test_task_comment_requires_login(self, client, task_with_access):
        """Test adding comment requires authentication."""
        url = reverse("tasks:add_comment", kwargs={"task_id": task_with_access.id})
        response = client.post(url, {"content": "Test comment"})
        assert response.status_code == 302

    def test_task_comment_success(self, client, user, task_with_access):
        """Test successful comment creation."""
        client.force_login(user)
        url = reverse("tasks:add_comment", kwargs={"task_id": task_with_access.id})

        response = client.post(url, {"content": "Test comment"}, HTTP_X_REQUESTED_WITH="XMLHttpRequest")

        assert response.status_code == 200
        assert TaskComment.objects.filter(task=task_with_access, content="Test comment").exists()
        assert TaskEvent.objects.filter(task=task_with_access, event_type="commented").exists()

    def test_task_comment_denied_without_permission(self, client):
        """Test comment is denied without permission."""
        unauthorized_user = UserFactory()
        task = TaskFactory()

        client.force_login(unauthorized_user)
        url = reverse("tasks:add_comment", kwargs={"task_id": task.id})

        response = client.post(url, {"content": "Test comment"}, HTTP_X_REQUESTED_WITH="XMLHttpRequest")

        assert response.status_code == 403
