from urllib.parse import urlencode

import httpx

from commcare_connect.opportunity.models import HQApiKey
from commcare_connect.utils.commcarehq_api import CommCareHQAPIException


def get_case_data(api_key: HQApiKey, domain: str, filters: dict[str, any]):
    params = urlencode(filters)
    url = f"{api_key.hq_server.url}/a/{domain}/api/case/v2/?{params}"
    response = httpx.get(url, headers={"Authorization": f"ApiKey {api_key.user.email}:{api_key.api_key}"})

    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise CommCareHQAPIException(f"Failed to fetch case data for {domain}. HQ Error: {e}")

    return response.json()


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

    return response.json()
