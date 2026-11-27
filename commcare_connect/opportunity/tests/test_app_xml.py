import tempfile
import zipfile
from pathlib import Path

import pytest

from commcare_connect.opportunity.app_xml import (
    XMLNS,
    Module,
    TaskUnit,
    extract_connect_blocks,
    get_connect_blocks_for_app,
    get_form_xml_for_app,
    get_task_units_for_app,
)
from commcare_connect.opportunity.tests.factories import CommCareAppFactory


@pytest.fixture
def demo_app_ccz_content():
    """Create a temporary CCZ file with the contents of the `demo_app` folder.
    Returns the contents of the CCZ file as bytes.
    """
    path = Path(__file__).parent / "data" / "demo_app"
    with tempfile.TemporaryFile() as file:
        with zipfile.ZipFile(file, "w") as f:
            for ccz_file in sorted(path.glob("**/*")):
                f.write(ccz_file, ccz_file.as_posix().removeprefix(path.as_posix()))

        file.seek(0)
        return file.read()


@pytest.mark.django_db
def test_get_form_xml_for_app(httpx_mock, demo_app_ccz_content):
    httpx_mock.add_response(content=demo_app_ccz_content)
    app = CommCareAppFactory(cc_domain="demo_domain", cc_app_id="app_id")
    form_xml = get_form_xml_for_app(app)
    assert len(form_xml) == 5
    assert "http://openrosa.org/formdesigner/52F02F3E-320D-4D91-9EBF-FF4F06226E98" in form_xml[0]
    assert "http://openrosa.org/formdesigner/EC1AD740-D2C9-4532-AECC-2D5CF5364696" in form_xml[1]
    assert "http://openrosa.org/formdesigner/11151AA2-1599-4C5E-8013-5E2197B6C68E" in form_xml[2]
    assert "http://openrosa.org/formdesigner/BD70B3D5-6CB4-4A2E-AD5B-C8E3E7BC37A7" in form_xml[3]


@pytest.mark.django_db
def test_get_connect_blocks_for_app(httpx_mock, demo_app_ccz_content):
    httpx_mock.add_response(content=demo_app_ccz_content)
    app = CommCareAppFactory(cc_domain="demo_domain", cc_app_id="app_id")
    blocks = get_connect_blocks_for_app(app)
    assert blocks == [
        Module(
            id="module_1",
            name="Module 1",
            description="This is the first module in a series of modules\n"
            "that will take you through all you need to know.",
            time_estimate=1,
            time_estimate_minutes=60,
        ),
        Module(
            id="module_2",
            name="Module 2",
            description="This is module 2 of the series.",
            time_estimate=2,
            time_estimate_minutes=120,
        ),
        Module(
            id="module_3",
            name="Module 3",
            description="Module 3 in the series",
            time_estimate=3,
            time_estimate_minutes=180,
        ),
    ]


@pytest.mark.django_db
def test_get_task_units_for_app(httpx_mock, demo_app_ccz_content):
    httpx_mock.add_response(content=demo_app_ccz_content)
    # get_task_units_for_app caches by cc_app_id in Redis, which outlives the test run, so let the
    # factory generate a unique one rather than reusing a key an earlier run may have cached.
    app = CommCareAppFactory(cc_domain="demo_domain")
    task_units = get_task_units_for_app(app)
    assert task_units == [
        TaskUnit(id="task_1", name="Task One", description="Description for task one"),
        TaskUnit(id="task_2", name="Task Two", description="Description for task two"),
    ]


@pytest.mark.django_db
def test_get_task_units_for_app_is_cached(httpx_mock, demo_app_ccz_content):
    httpx_mock.add_response(content=demo_app_ccz_content)
    # A fresh cc_app_id, so the first call cannot be a cache hit left by an earlier run.
    app = CommCareAppFactory(cc_domain="demo_domain")

    first = get_task_units_for_app(app)
    second = get_task_units_for_app(app)

    assert second == first
    assert len(httpx_mock.get_requests()) == 1


@pytest.mark.parametrize(
    "estimate_xml, expected_hours, expected_minutes",
    [
        ("<time_estimate>2</time_estimate>", 2, 120),
        ("<time_estimate>1</time_estimate><time_estimate_minutes>10</time_estimate_minutes>", 1, 10),
        ("<time_estimate_minutes>90</time_estimate_minutes>", 2, 90),
    ],
)
def test_extract_module_time_estimate(estimate_xml, expected_hours, expected_minutes):
    form_xml = (
        f'<data><module xmlns="{XMLNS}" id="m1"><name>M</name><description>D</description>'
        f"{estimate_xml}</module></data>"
    )
    assert list(extract_connect_blocks(form_xml)) == [
        Module(
            id="m1", name="M", description="D", time_estimate=expected_hours, time_estimate_minutes=expected_minutes
        )
    ]
