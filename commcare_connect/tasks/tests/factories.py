import factory
from factory.django import DjangoModelFactory

from commcare_connect.opportunity.tests.factories import OpportunityFactory
from commcare_connect.tasks.models import Task, TaskAISession, TaskComment, TaskEvent
from commcare_connect.users.tests.factories import UserFactory


class TaskFactory(DjangoModelFactory):
    class Meta:
        model = Task

    user = factory.SubFactory(UserFactory)
    opportunity = factory.SubFactory(OpportunityFactory)
    created_by_user = factory.SubFactory(UserFactory)
    task_type = "warning"
    status = "unassigned"
    priority = "medium"
    title = factory.Sequence(lambda n: f"Task {n}")
    description = factory.Faker("text")
    created_by = factory.LazyAttribute(lambda obj: obj.created_by_user.email if obj.created_by_user else "system")
    modified_by = factory.LazyAttribute(lambda obj: obj.created_by_user.email if obj.created_by_user else "system")


class TaskEventFactory(DjangoModelFactory):
    class Meta:
        model = TaskEvent

    task = factory.SubFactory(TaskFactory)
    event_type = "created"
    actor = factory.Faker("name")
    description = factory.Faker("sentence")
    created_by = "system"
    modified_by = "system"


class TaskCommentFactory(DjangoModelFactory):
    class Meta:
        model = TaskComment

    task = factory.SubFactory(TaskFactory)
    author = factory.SubFactory(UserFactory)
    content = factory.Faker("text")
    created_by = factory.LazyAttribute(lambda obj: obj.author.email)
    modified_by = factory.LazyAttribute(lambda obj: obj.author.email)


class TaskAISessionFactory(DjangoModelFactory):
    class Meta:
        model = TaskAISession

    task = factory.SubFactory(TaskFactory)
    ocs_session_id = factory.Sequence(lambda n: f"ocs-session-{n}")
    status = "initiated"
    created_by = "system"
    modified_by = "system"
