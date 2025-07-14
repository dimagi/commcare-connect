import itertools
import re
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass

import httpx

from commcare_connect.opportunity.models import CommCareApp
from commcare_connect.utils.commcarehq_api import CommCareHQAPIException

XMLNS = "http://commcareconnect.com/data/v1/learn"
XMLNS_PREFIX = "{%s}" % XMLNS


@dataclass
class Module:
    id: str
    name: str
    description: str
    time_estimate: int


@dataclass
class DeliverUnit:
    id: str
    name: str


class AppNoBuildException(CommCareHQAPIException):
    pass


def get_connect_blocks_for_app(learn_app) -> list[Module]:
    form_xmls = get_form_xml_for_app(learn_app)
    return list(itertools.chain.from_iterable(extract_connect_blocks(form_xml) for form_xml in form_xmls))


def get_deliver_units_for_app(deliver_app) -> list[DeliverUnit]:
    form_xmls = get_form_xml_for_app(deliver_app)
    return list(itertools.chain.from_iterable(extract_deliver_units(form_xml) for form_xml in form_xmls))


def get_form_xml_for_app(app: CommCareApp) -> list[str]:
    """Download the CCZ for the given app and return the XML for each form."""
    app_id = app.cc_app_id
    domain = app.cc_domain
    url = app.hq_server.url

    for latest in ["release", "build", "save"]:
        ccz_url = f"{url}/a/{domain}/apps/api/download_ccz/"
        params = {
            "app_id": app_id,
            "latest": latest,
        }
        response = httpx.get(ccz_url, params=params, timeout=30)
        if not response.is_success:
            continue

        form_xml = []
        with tempfile.NamedTemporaryFile() as file:
            file.write(response.content)
            file.seek(0)

            form_re = re.compile(r"modules-\d+/forms-\d+\.xml")
            with zipfile.ZipFile(file, "r") as zip_ref:
                for file in zip_ref.namelist():
                    if form_re.match(file):
                        with zip_ref.open(file) as xml_file:
                            form_xml.append(xml_file.read().decode())
        return form_xml

    raise AppNoBuildException(f"App {app_id} has no builds available.")


def extract_connect_blocks(form_xml):
    xml = ET.fromstring(form_xml)
    yield from extract_modules(xml)


def extract_modules(xml: ET.Element):
    for block in xml.findall(f".//{XMLNS_PREFIX}module"):
        slug = block.get("id")
        name = get_element_text(block, "name")
        description = get_element_text(block, "description")
        time_estimate = get_element_text(block, "time_estimate")
        yield Module(slug, name, description, int(time_estimate) if time_estimate is not None else None)


def extract_deliver_units(form_xml):
    xml = ET.fromstring(form_xml)
    yield from extract_deliver_unit(xml)


def extract_deliver_unit(xml: ET.Element):
    for block in xml.findall(f".//{XMLNS_PREFIX}deliver"):
        slug = block.get("id")
        name = get_element_text(block, "name")
        yield DeliverUnit(slug, name)


def get_element_text(parent, name) -> str | None:
    element = parent.find(f"{XMLNS_PREFIX}{name}")
    return element.text if element is not None else None
