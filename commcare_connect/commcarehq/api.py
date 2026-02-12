import dataclasses
from typing import TypedDict
from urllib.parse import urlencode

import httpx

from commcare_connect.opportunity.models import HQApiKey, OpportunityAccess
from commcare_connect.users.models import ConnectIDUserLink
from commcare_connect.utils.commcarehq_api import CommCareHQAPIException


class GetCaseDataAPIFilters(TypedDict):
    case_type: str
    case_name: str


@dataclasses.dataclass
class CommCareCase:
    domain: str
    case_id: str
    case_type: str
    case_name: str
    external_id: str | None
    owner_id: str
    date_opened: str
    last_modified: str
    server_last_modified: str
    indexed_on: str
    closed: bool
    date_closed: str | None
    properties: dict[str, str]
    indices: dict[str, any]


def get_case_data(api_key: HQApiKey, domain: str, filters: GetCaseDataAPIFilters) -> list[CommCareCase]:
    params = urlencode(filters)
    url_queue = [f"/a/{domain}/api/case/v2/?{params}"]

    def get_page(page_url):
        with httpx.Client(
            base_url=api_key.hq_server.url,
            headers={"Authorization": f"ApiKey {api_key.user.email}:{api_key.api_key}"},
        ) as client:
            response = client.get(page_url)

            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as e:
                raise CommCareHQAPIException(f"Failed to fetch case data for {domain}. HQ Error: {e}")

            data = response.json()
            next_url = data.get("next")
            if next_url is not None:
                url_queue.append(next_url)
            return [CommCareCase(**case_data) for case_data in data.get("cases", [])]

    cases = []
    while len(url_queue):
        url = url_queue.pop()
        cases.extend(get_page(url))
    return cases


def update_case_data_by_case_id(api_key: HQApiKey, domain: str, case_id: str, data: dict[str, any]) -> dict[str, any]:
    url = f"{api_key.hq_server.url}/a/{domain}/api/case/v2/{case_id}/"
    response = httpx.put(
        url,
        headers={"Authorization": f"ApiKey {api_key.user.email}:{api_key.api_key}"},
        json=data,
    )

    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise CommCareHQAPIException(f"Failed to update case data for {domain} with {case_id}. HQ Error: {e}")

    data = response.json()
    return CommCareCase(**data.get("case", {}))


def get_usercase(opportunity_access: OpportunityAccess) -> CommCareCase | None:
    domain = opportunity_access.opportunity.deliver_app.cc_domain
    api_key = opportunity_access.opportunity.api_key
    user = opportunity_access.user
    case_data = get_case_data(
        api_key,
        domain,
        filters={
            "case_type": "commcare-user",
            "case_name": user.username.lower(),
        },
    )
    return next(iter(case_data), None)


def update_usercase(opportunity_access: OpportunityAccess, data: dict[str, any]) -> dict[str, any]:
    domain = opportunity_access.opportunity.deliver_app.cc_domain
    api_key = opportunity_access.opportunity.api_key
    hq_server = api_key.hq_server

    link = ConnectIDUserLink.objects.get(user=opportunity_access.user, domain=domain, hq_server=hq_server)
    if not link.hq_case_id:
        usercase = get_usercase(opportunity_access)
        if usercase is not None:
            link.hq_case_id = usercase.case_id
            link.save()

    return update_case_data_by_case_id(api_key, domain, link.hq_case_id, data)
