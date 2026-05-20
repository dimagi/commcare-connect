import dataclasses
from typing import Any, TypedDict
from urllib.parse import urlencode

import httpx
from django.db import transaction

from commcare_connect.microplanning.models import WorkArea
from commcare_connect.microplanning.serializers import WorkAreaCaseSerializer
from commcare_connect.opportunity.models import HQApiKey, Opportunity, OpportunityAccess
from commcare_connect.users.helpers import fetch_hq_user_uuid
from commcare_connect.users.models import ConnectIDUserLink
from commcare_connect.utils.commcarehq_api import CommCareHQAPIException

HQ_CASE_BULK_CHUNK_SIZE = 100


GetCaseDataAPIFilters = TypedDict(
    "GetCaseDataAPIFilters",
    {
        "case_type": str,
        "properties.username": str,
    },
)


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
    indices: dict[str, Any]


def get_case_list(api_key: HQApiKey, domain: str, filters: GetCaseDataAPIFilters) -> list[CommCareCase]:
    params = urlencode(filters)
    url = f"/a/{domain}/api/case/v2/?{params}"

    cases = []
    with httpx.Client(
        base_url=api_key.hq_server.url,
        headers={"Authorization": f"ApiKey {api_key.user.email}:{api_key.api_key}"},
    ) as client:
        while url is not None:
            try:
                response = client.get(url)
                response.raise_for_status()
            except (httpx.HTTPStatusError, httpx.RequestError) as e:
                raise CommCareHQAPIException(f"Failed to fetch case data for {domain}. HQ Error: {e}") from e

            data = response.json()
            cases.extend(CommCareCase(**case_data) for case_data in data.get("cases", []))
            url = data.get("next")
    return cases


def create_or_update_case_by_work_area(work_area: WorkArea) -> CommCareCase:
    if not work_area.opportunity_access:
        raise ValueError("Work Area must have an assigned Opportunity Access")

    opp_access = work_area.opportunity_access
    api_key = opp_access.opportunity.api_key
    domain = opp_access.opportunity.deliver_app.cc_domain
    user = opp_access.user
    case_data = WorkAreaCaseSerializer(work_area).data
    case_data["owner_id"] = _get_hq_user_uuid_for_owner(user, domain, api_key)

    with transaction.atomic():
        # Re-fetch with a row-level lock to prevent a race condition where two
        # concurrent calls both see case_id as None
        locked_work_area = WorkArea.objects.select_for_update().get(pk=work_area.pk)
        case = create_or_update_case(api_key, domain, case_data, case_id=locked_work_area.case_id)
        if locked_work_area.case_id is None:
            locked_work_area.case_id = case.case_id
            locked_work_area.save(update_fields=["case_id"])
    return case


def _get_hq_user_uuid_for_owner(user, domain, api_key):
    link = ConnectIDUserLink.objects.filter(commcare_username=user.username.lower()).first()
    return _resolve_hq_user_uuid(user, domain, api_key, link)


def _resolve_hq_user_uuid(user, domain, api_key, link):
    if link and link.hq_user_uuid:
        return link.hq_user_uuid

    hq_user_uuid = fetch_hq_user_uuid(user.username, domain, api_key)
    if hq_user_uuid is None:
        raise CommCareHQAPIException(f"Failed to find HQ user for {user.username.lower()} on {domain} HQ domain.")
    if link:
        link.hq_user_uuid = hq_user_uuid
        link.save(update_fields=["hq_user_uuid"])
    return hq_user_uuid


def bulk_create_or_update_cases_by_work_areas(
    work_areas: list[WorkArea], opportunity: Opportunity
) -> list[CommCareCase]:
    """Sync a batch of work areas to HQ in a single UPSERT call keyed on external_id."""
    if not work_areas:
        return []

    api_key = opportunity.api_key
    domain = opportunity.deliver_app.cc_domain

    wa_by_username: dict[str, WorkArea] = {wa.opportunity_access.user.username.lower(): wa for wa in work_areas}
    links_by_username: dict[str, ConnectIDUserLink] = {
        link.commcare_username: link
        for link in ConnectIDUserLink.objects.filter(commcare_username__in=wa_by_username.keys())
    }
    owner_id_by_username: dict[str, str] = {
        username: _resolve_hq_user_uuid(wa.opportunity_access.user, domain, api_key, links_by_username.get(username))
        for username, wa in wa_by_username.items()
    }

    cases_data = []
    for wa in work_areas:
        case_data = dict(WorkAreaCaseSerializer(wa).data)
        case_data["owner_id"] = owner_id_by_username[wa.opportunity_access.user.username.lower()]
        case_data["create"] = None  # UPSERT: HQ decides create vs update via external_id
        cases_data.append(case_data)

    cases = bulk_create_or_update_cases(api_key, domain, cases_data)

    wa_by_id = {str(wa.pk): wa for wa in work_areas if wa.case_id is None}
    newly_created = []
    for case in cases:
        if case.external_id in wa_by_id:
            wa = wa_by_id[case.external_id]
            wa.case_id = case.case_id
            newly_created.append(wa)
    if newly_created:
        WorkArea.objects.bulk_update(newly_created, ["case_id"])

    return cases


def bulk_create_or_update_cases(
    api_key: HQApiKey,
    domain: str,
    cases_data: list[dict[str, Any]],
) -> list[CommCareCase]:
    url = f"{api_key.hq_server.url}/a/{domain}/api/case/v2/"
    headers = {"Authorization": f"ApiKey {api_key.user.email}:{api_key.api_key}"}
    cases = []
    with httpx.Client(headers=headers) as client:
        for i in range(0, len(cases_data), HQ_CASE_BULK_CHUNK_SIZE):
            chunk = cases_data[i : i + HQ_CASE_BULK_CHUNK_SIZE]  # noqa: E203
            try:
                response = client.post(url, json=chunk)
                response.raise_for_status()
            except (httpx.HTTPStatusError, httpx.RequestError) as e:
                raise CommCareHQAPIException(f"Failed to bulk update cases for {domain}. HQ Error: {e}") from e
            cases.extend(CommCareCase(**case_data) for case_data in response.json().get("cases", []))
    return cases


def create_or_update_case(
    api_key: HQApiKey,
    domain: str,
    case_data: dict[str, Any],
    case_id: str | None = None,
) -> CommCareCase:
    base_url = f"{api_key.hq_server.url}/a/{domain}/api/case/v2/"
    headers = {"Authorization": f"ApiKey {api_key.user.email}:{api_key.api_key}"}

    try:
        with httpx.Client(base_url=base_url, headers=headers) as client:
            if case_id:
                error_msg = f"Failed to update case data for {domain} with {case_id}."
                response = client.put(f"{case_id}/", json=case_data)
            else:
                error_msg = f"Failed to create case for {domain}."
                response = client.post("", json=case_data)
        response.raise_for_status()
    except (httpx.HTTPStatusError, httpx.RequestError) as e:
        raise CommCareHQAPIException(f"{error_msg} HQ Error: {e}") from e

    data = response.json()
    return CommCareCase(**data.get("case", {}))


def bulk_update_usercases(updates: dict[OpportunityAccess, dict[str, Any]]) -> None:
    """Update usercase properties on CommCare HQ for multiple users in a single bulk request.

    All entries in `updates` must belong to the same opportunity. The domain, API key, and
    HQ server are derived from the first entry and applied to the entire batch.
    """
    if not updates:
        return

    first_access = next(iter(updates))
    domain = first_access.opportunity.deliver_app.cc_domain
    api_key = first_access.opportunity.api_key
    hq_server = api_key.hq_server

    users = [access.user for access in updates]
    links = ConnectIDUserLink.objects.filter(user__in=users, domain=domain, hq_server=hq_server)
    links_by_user = {link.user_id: link for link in links}

    cases_data = []
    for access, data in updates.items():
        link = links_by_user.get(access.user_id)
        if link is None:
            hq_case_id = get_usercase(access).case_id
        elif link.hq_case_id is None:
            hq_case_id = get_usercase(access).case_id
            link.hq_case_id = hq_case_id
            link.save()
        else:
            hq_case_id = link.hq_case_id
        cases_data.append({"case_id": hq_case_id, "create": False, **data})

    bulk_create_or_update_cases(api_key, domain, cases_data)


def get_usercase(opportunity_access: OpportunityAccess) -> CommCareCase:
    domain = opportunity_access.opportunity.deliver_app.cc_domain
    api_key = opportunity_access.opportunity.api_key
    user = opportunity_access.user
    case_data = get_case_list(
        api_key,
        domain,
        filters={
            "case_type": "commcare-user",
            "properties.username": user.username.lower(),
        },
    )
    usercase = next(iter(case_data), None)
    if usercase is None:
        raise CommCareHQAPIException(f"Failed to find usercase for {user.username.lower()} on {domain} HQ domain.")
    return usercase
