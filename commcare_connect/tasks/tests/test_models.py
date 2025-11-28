import pytest

from commcare_connect.opportunity.tests.factories import OpportunityAccessFactory, OpportunityFactory
from commcare_connect.tasks.tests.factories import (
    TaskAISessionFactory,
    TaskCommentFactory,
    TaskEventFactory,
    TaskFactory,
)
from commcare_connect.users.tests.factories import MembershipFactory, UserFactory


@pytest.mark.django_db
class TestTaskModel:
    def test_task_creation(self):
        """Test basic task creation."""
        task = TaskFactory()
        assert task.id is not None
        assert task.user is not None
        assert task.opportunity is not None
        assert task.task_type in ["warning", "deactivation"]
        assert task.status in [
            "unassigned",
            "network_manager",
            "program_manager",
            "action_underway",
            "resolved",
            "closed",
        ]

    def test_task_str_representation(self):
        """Test task string representation."""
        task = TaskFactory(id=123, task_type="warning")
        assert "Task #123" in str(task)
        assert task.user.name in str(task)

    def test_task_can_user_access_superuser(self):
        """Test superuser can access any task."""
        task = TaskFactory()
        superuser = UserFactory(is_superuser=True)
        assert task.can_user_access(superuser)

    def test_task_can_user_access_via_opportunity(self):
        """Test user can access task via opportunity access."""
        user = UserFactory()
        opportunity = OpportunityFactory()
        OpportunityAccessFactory(user=user, opportunity=opportunity)
        task = TaskFactory(opportunity=opportunity)
        assert task.can_user_access(user)

    def test_task_can_user_access_via_organization(self):
        """Test user can access task via organization membership."""
        user = UserFactory()
        opportunity = OpportunityFactory()
        MembershipFactory(user=user, organization=opportunity.organization)
        task = TaskFactory(opportunity=opportunity)
        assert task.can_user_access(user)

    def test_task_cannot_access_without_permission(self):
        """Test user cannot access task without proper permissions."""
        task = TaskFactory()
        unauthorized_user = UserFactory()
        assert not task.can_user_access(unauthorized_user)


@pytest.mark.django_db
class TestTaskEventModel:
    def test_task_event_creation(self):
        """Test task event creation."""
        task = TaskFactory()
        event = TaskEventFactory(task=task, event_type="status_changed")
        assert event.id is not None
        assert event.task == task
        assert event.event_type == "status_changed"

    def test_task_event_ordering(self):
        """Test task events are ordered by date_created descending."""
        task = TaskFactory()
        event1 = TaskEventFactory(task=task)
        event2 = TaskEventFactory(task=task)

        events = task.events.all()
        assert events[0].id == event2.id
        assert events[1].id == event1.id


@pytest.mark.django_db
class TestTaskCommentModel:
    def test_task_comment_creation(self):
        """Test task comment creation."""
        task = TaskFactory()
        comment = TaskCommentFactory(task=task)
        assert comment.id is not None
        assert comment.task == task
        assert comment.author is not None
        assert comment.content

    def test_task_comment_str_representation(self):
        """Test comment string representation."""
        comment = TaskCommentFactory()
        assert comment.author.name in str(comment)
        assert f"Task #{comment.task.id}" in str(comment)


@pytest.mark.django_db
class TestTaskAISessionModel:
    def test_task_ai_session_creation(self):
        """Test AI session creation."""
        task = TaskFactory()
        session = TaskAISessionFactory(task=task, ocs_session_id="test-session-123")
        assert session.id is not None
        assert session.task == task
        assert session.ocs_session_id == "test-session-123"
        assert session.status == "initiated"

    def test_task_ai_session_unique_session_id(self):
        """Test OCS session ID must be unique."""
        TaskAISessionFactory(ocs_session_id="unique-session")

        with pytest.raises(Exception):
            TaskAISessionFactory(ocs_session_id="unique-session")
