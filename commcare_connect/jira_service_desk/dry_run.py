"""A stand-in for the service desk, for trying the Finance request flow locally.

Enabled by JIRA_SERVICE_DESK_DRY_RUN, which only local settings read from the environment. Nothing
is sent to Atlassian: uploads are discarded and each request is logged and given a fake key, or
failed the way Jira would fail it.
"""

import json
import logging
import time
import uuid

import httpx
from django.conf import settings

logger = logging.getLogger(__name__)

SUCCESS = "success"
REJECT = "reject"
TIMEOUT = "timeout"
UNREACHABLE = "unreachable"
MODES = {SUCCESS, REJECT, TIMEOUT, UNREACHABLE}
OFF_VALUES = {"", "0", "false", "off", "no"}
ON_VALUES = {"1", "true", "on", "yes"}


def get_mode() -> str | None:
    value = str(settings.JIRA_SERVICE_DESK_DRY_RUN or "").strip().lower()
    if value in OFF_VALUES:
        return None
    if value in ON_VALUES:
        return SUCCESS
    if value not in MODES:
        raise ValueError(f"JIRA_SERVICE_DESK_DRY_RUN must be one of {sorted(MODES)} or true/false, not {value!r}")
    return value


def attach_temporary_file(filename: str, content: bytes) -> str:
    logger.info("Jira dry run: discarded upload of %s (%s bytes)", filename, len(content))
    return f"dry-run-{uuid.uuid4()}"


def create_request(mode: str, payload: dict) -> tuple[str, str]:
    """Log the request and return a fake (key, url), or raise the error Jira would raise in `mode`."""
    time.sleep(settings.JIRA_SERVICE_DESK_DRY_RUN_DELAY)
    logger.info("Jira dry run (%s): request that would be sent:\n%s", mode, json.dumps(payload, indent=2))
    request = httpx.Request("POST", "https://jira.dry-run.invalid/request")
    if mode == REJECT:
        response = httpx.Response(422, json={"errorMessage": "Dry run: Jira rejected this request."}, request=request)
        raise httpx.HTTPStatusError("Dry run rejection", request=request, response=response)
    if mode == TIMEOUT:
        raise httpx.ReadTimeout("Dry run: Jira did not respond", request=request)
    if mode == UNREACHABLE:
        raise httpx.ConnectError("Dry run: Jira could not be reached", request=request)
    key = f"DRY-RUN-{uuid.uuid4().hex[:6].upper()}"
    return key, f"https://example.com/jira-dry-run/{key}"
