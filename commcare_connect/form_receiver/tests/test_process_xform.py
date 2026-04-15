import datetime
from contextlib import ExitStack, contextmanager
from unittest import mock

import pytest
from django.utils.timezone import now

from commcare_connect.form_receiver.processor import (
    ASSESSMENT_JSONPATH,
    _get_matching_blocks,
    process_assessments,
    process_deliver_form,
    process_learn_form,
    process_task_modules,
)
from commcare_connect.form_receiver.tests.xforms import (
    AssessmentStubFactory,
    DeliverUnitStubFactory,
    LearnModuleJsonFactory,
    TaskJsonFactory,
    get_form_model,
)
from commcare_connect.opportunity.models import AssignedTask, AssignedTaskStatus, OpportunityAccess, TaskType
from commcare_connect.opportunity.tests.factories import CommCareAppFactory, OpportunityAccessFactory

LEARN_PROCESSOR_PATCHES = [
    "commcare_connect.form_receiver.processor.process_learn_modules",
    "commcare_connect.form_receiver.processor.process_assessments",
]

DELIVER_PROCESSOR_PATCHES = [
    "commcare_connect.form_receiver.processor.process_deliver_unit",
    "commcare_connect.form_receiver.processor.process_task_modules",
]


def test_process_learn_form_no_matching_blocks():
    with mock.patch(
        "commcare_connect.form_receiver.processor.process_learn_modules"
    ) as process_learn_modules, mock.patch(
        "commcare_connect.form_receiver.processor.process_assessments"
    ) as process_assessments:
        process_learn_form(None, get_form_model(), None, None)
    assert process_learn_modules.call_count == 0
    assert process_assessments.call_count == 0


def test_process_learn_module():
    learn_module = LearnModuleJsonFactory().json
    xform = get_form_model(form_block=learn_module)
    with patch_multiple(*LEARN_PROCESSOR_PATCHES) as [process_learn_module, process_assessment]:
        process_learn_form(None, xform, None, None)
    assert process_learn_module.call_count == 1
    assert process_assessment.call_count == 0


def test_process_assessment():
    assessment = AssessmentStubFactory().json
    xform = get_form_model(form_block=assessment)
    with patch_multiple(*LEARN_PROCESSOR_PATCHES) as [process_learn_module, process_assessment]:
        process_learn_form(None, xform, None, None)
    assert process_learn_module.call_count == 0
    assert process_assessment.call_count == 1


def test_process_deliver_form():
    deliver_block = DeliverUnitStubFactory().json
    xform = get_form_model(form_block=deliver_block)
    with patch_multiple(*DELIVER_PROCESSOR_PATCHES) as [process_deliver_unit, process_task_module]:
        process_deliver_form(None, xform, None, None)
    assert process_deliver_unit.call_count == 1
    assert process_task_module.call_count == 0


def test_process_deliver_form_no_matches():
    xform = get_form_model()
    with patch_multiple(*DELIVER_PROCESSOR_PATCHES) as [process_deliver_unit, process_task_module]:
        process_deliver_form(None, xform, None, None)
    assert process_deliver_unit.call_count == 0
    assert process_task_module.call_count == 0


@pytest.mark.django_db
class TestProcessTaskModules:
    @pytest.fixture
    def task_module_context(self, mobile_user_with_connect_link, opportunity):
        access = OpportunityAccess.objects.get(user=mobile_user_with_connect_link, opportunity=opportunity)
        existing_last_active = now()
        access.last_active = existing_last_active
        access.save(update_fields=["last_active"])
        return {
            "user": mobile_user_with_connect_link,
            "opportunity": opportunity,
            "access": access,
            "xform": get_form_model(),
            "existing_last_active": existing_last_active,
        }

    def _process(self, context, blocks):
        process_task_modules(
            context["user"],
            context["xform"],
            context["opportunity"].deliver_app,
            context["opportunity"],
            blocks,
        )

    def _assert_last_active_unchanged(self, context):
        context["access"].refresh_from_db()
        assert context["access"].last_active == context["existing_last_active"]

    def test_updates_assigned_tasks(self, task_module_context):
        context = task_module_context
        earlier_last_active = now() - datetime.timedelta(days=2)
        context["access"].last_active = earlier_last_active
        context["access"].save(update_fields=["last_active"])
        context["existing_last_active"] = earlier_last_active

        task_type = TaskType.objects.create(
            app=context["opportunity"].deliver_app,
            slug="task-one",
            name="Task 1",
            description="desc",
        )
        assigned_task = AssignedTask.objects.create(
            task_type=task_type,
            opportunity_access=context["access"],
            completed_at=now() - datetime.timedelta(days=1),
            duration=datetime.timedelta(minutes=5),
            xform_id=None,
            status=AssignedTaskStatus.ASSIGNED,
            due_date=now() + datetime.timedelta(days=7),
        )

        task_block = TaskJsonFactory(id=task_type.slug).json
        context["xform"] = get_form_model(form_block=task_block)

        self._process(context, [task_block["task"]])

        assigned_task.refresh_from_db()
        context["access"].refresh_from_db()

        assert assigned_task.status == AssignedTaskStatus.COMPLETED
        assert assigned_task.xform_id == context["xform"].id
        assert assigned_task.completed_at == context["xform"].metadata.timeEnd
        assert assigned_task.duration == context["xform"].metadata.duration

    def test_missing_task(self, task_module_context):
        task_block = TaskJsonFactory(id="unknown-task").json["task"]
        self._process(task_module_context, [task_block])
        assert AssignedTask.objects.filter(opportunity_access=task_module_context["access"]).count() == 0
        self._assert_last_active_unchanged(task_module_context)

    def test_unassigned_task(self, task_module_context):
        opportunity = task_module_context["opportunity"]
        task_type = TaskType.objects.create(
            app=opportunity.deliver_app,
            slug="needs-assignment",
            name="Needs Assignment",
            description="desc",
        )
        other_access = OpportunityAccessFactory(opportunity=opportunity)
        AssignedTask.objects.create(
            task_type=task_type,
            opportunity_access=other_access,
            completed_at=now(),
            duration=datetime.timedelta(minutes=5),
            xform_id=None,
            status=AssignedTaskStatus.ASSIGNED,
            due_date=now() + datetime.timedelta(days=7),
        )
        task_block = TaskJsonFactory(id=task_type.slug).json["task"]
        self._process(task_module_context, [task_block])
        assert AssignedTask.objects.filter(opportunity_access=task_module_context["access"]).count() == 0
        self._assert_last_active_unchanged(task_module_context)

    def test_already_assigned_task(self, task_module_context):
        context_access = task_module_context["access"]
        opportunity = task_module_context["opportunity"]
        task_type = TaskType.objects.create(
            app=opportunity.deliver_app,
            slug="already-completed-task",
            name="Completed Task",
            description="desc",
        )
        existing_assigned_task = AssignedTask.objects.create(
            task_type=task_type,
            opportunity_access=context_access,
            completed_at=now(),
            duration=datetime.timedelta(minutes=5),
            xform_id="existing-form-id",
            status=AssignedTaskStatus.COMPLETED,
            due_date=now() + datetime.timedelta(days=7),
        )
        task_block = TaskJsonFactory(id=task_type.slug).json["task"]
        self._process(task_module_context, [task_block, {"name": "missing @id"}])
        existing_assigned_task.refresh_from_db()
        assert existing_assigned_task.status == AssignedTaskStatus.COMPLETED
        assert existing_assigned_task.xform_id == "existing-form-id"
        assert AssignedTask.objects.filter(opportunity_access=context_access).count() == 1
        self._assert_last_active_unchanged(task_module_context)


@pytest.mark.django_db
@mock.patch("commcare_connect.form_receiver.processor.notify_user_for_scored_assessment.delay")
def test_process_assessments(notification_patch):
    app = CommCareAppFactory()
    opportunity_access = OpportunityAccessFactory()
    assessment_form = AssessmentStubFactory().json
    xform = get_form_model(form_block=assessment_form)
    matches = _get_matching_blocks(ASSESSMENT_JSONPATH, xform)

    process_assessments(opportunity_access.user, xform, app, opportunity_access.opportunity, matches)

    user_assessment = opportunity_access.user.assessments.first()

    assert notification_patch.call_count == 1
    notification_patch.assert_called_with(user_assessment.pk)


@contextmanager
def patch_multiple(*args):
    with ExitStack() as stack:
        patches = [stack.enter_context(mock.patch(arg)) for arg in args]
        yield patches
