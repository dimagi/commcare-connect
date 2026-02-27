from contextlib import ExitStack, contextmanager
from unittest import mock

from commcare_connect.form_receiver.processor import process_deliver_form, process_learn_form
from commcare_connect.form_receiver.tests.xforms import (
    AssessmentStubFactory,
    DeliverUnitStubFactory,
    LearnModuleJsonFactory,
    TaskJsonFactory,
    get_form_model,
)

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


def test_process_task_module():
    task_block = TaskJsonFactory().json
    xform = get_form_model(form_block=task_block)
    with patch_multiple(*DELIVER_PROCESSOR_PATCHES) as [process_deliver_unit, process_task_module]:
        process_deliver_form(None, xform, None, None)
    assert process_deliver_unit.call_count == 0
    assert process_task_module.call_count == 1


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


@contextmanager
def patch_multiple(*args):
    with ExitStack() as stack:
        patches = [stack.enter_context(mock.patch(arg)) for arg in args]
        yield patches
