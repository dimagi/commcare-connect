import dataclasses
from typing import TypedDict
from urllib.parse import urlencode

import httpx

from commcare_connect.opportunity.models import HQApiKey, OpportunityAccess
from commcare_connect.users.models import ConnectIDUserLink, User
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
    client = httpx.Client(
        base_url=api_key.hq_server.url, headers={"Authorization": f"ApiKey {api_key.user.email}:{api_key.api_key}"}
    )
    response = client.get(f"/a/{domain}/api/case/v2/?{params}")

    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise CommCareHQAPIException(f"Failed to fetch case data for {domain}. HQ Error: {e}")

    data = response.json()
    cases = [CommCareCase(**case_data) for case_data in data.get("cases", [])]

    while True:
        next_url = data.get("next")
        if next_url is None:
            break
        data = client.get(next_url)
        for case_data in data.get("cases", []):
            cases.append(CommCareCase(**case_data))
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


def get_usercase(api_key: HQApiKey, user: User, domain: str) -> CommCareCase:
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
        usercase = get_usercase(api_key, opportunity_access.user, domain)
        if usercase is not None:
            link.hq_case_id = usercase.case_id
            link.save()

    return update_case_data_by_case_id(api_key, domain, link.hq_case_id, data)
