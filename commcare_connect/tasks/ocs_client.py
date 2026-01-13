"""
OCS (Open Chat Studio) API client for AI assistant integration.

This module handles communication with the OCS API for:
- Triggering bot conversations
- Fetching conversation transcripts
"""

import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


class OCSClientError(Exception):
    """Base exception for OCS client errors."""

    pass


def get_ocs_config():
    """Get OCS configuration from Django settings."""
    return {
        "base_url": getattr(settings, "OCS_BASE_URL", ""),
        "api_key": getattr(settings, "OCS_API_KEY", ""),
    }


def trigger_bot(
    identifier, platform, bot_id, prompt_text, start_new_session=False, session_data=None, participant_data=None
):
    """
    Trigger an OCS bot conversation.

    Args:
        identifier: Unique identifier for the participant (e.g., phone number, user ID, UUID)
        platform: Channel platform to send the message through (e.g., 'commcare_connect', 'whatsapp', 'telegram')
        bot_id: OCS Bot ID (experiment UUID) to trigger
        prompt_text: Instructions for the bot (user won't see this)
        start_new_session: Whether to start a new session or continue existing (default: False)
        session_data: Custom data to store with the session (default: None)
        participant_data: Custom data to store with the participant (default: None)

    Returns:
        Empty dict on success (OCS API returns empty response)

    Raises:
        OCSClientError: If the API call fails
    """
    config = get_ocs_config()

    if not all([config["base_url"], config["api_key"]]):
        raise OCSClientError("OCS configuration is incomplete. Check OCS_BASE_URL and OCS_API_KEY settings.")

    if not bot_id:
        raise OCSClientError("Bot ID (experiment) is required.")

    if not identifier:
        raise OCSClientError("Participant identifier is required.")

    if not prompt_text:
        raise OCSClientError("Prompt text is required.")

    url = f"{config['base_url']}/api/trigger_bot"
    headers = {
        "X-Api-Key": config["api_key"],
        "Content-Type": "application/json",
    }

    payload = {
        "identifier": identifier,
        "platform": platform,
        "experiment": bot_id,
        "prompt_text": prompt_text,
        "start_new_session": start_new_session,
    }

    if session_data:
        payload["session_data"] = session_data

    if participant_data:
        payload["participant_data"] = participant_data

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=30)
        response.raise_for_status()

        # Parse response - OCS may return session info
        try:
            response_data = response.json() if response.text else {}
        except ValueError:
            response_data = {}

        logger.info(f"Successfully triggered bot {bot_id} for participant {identifier} on platform {platform}")
        return response_data

    except requests.exceptions.RequestException as e:
        logger.error(f"OCS API error when triggering bot: {e}")
        if hasattr(e, "response") and e.response is not None:
            try:
                error_detail = e.response.json()
                raise OCSClientError(f"Failed to trigger bot: {error_detail}")
            except ValueError:
                raise OCSClientError(f"Failed to trigger bot: {e.response.text}")
        raise OCSClientError(f"Failed to trigger bot: {str(e)}")


def get_transcript(session_id, since=None, limit=None):
    """
    Fetch conversation transcript from OCS.

    Args:
        session_id: OCS session ID (external UUID)
        since: Optional ISO datetime - only return messages after this timestamp
        limit: Optional integer - maximum number of messages to return

    Returns:
        dict with transcript data including messages array

    Raises:
        OCSClientError: If the API call fails
    """
    config = get_ocs_config()

    if not all([config["base_url"], config["api_key"]]):
        raise OCSClientError("OCS configuration is incomplete. Check OCS_BASE_URL and OCS_API_KEY settings.")

    # Use the complete session endpoint (includes all messages)
    url = f"{config['base_url']}/api/sessions/{session_id}"
    headers = {
        "X-Api-Key": config["api_key"],
    }

    # Add optional query parameters
    params = {}
    if since:
        params["since"] = since
    if limit:
        params["limit"] = limit

    try:
        response = requests.get(url, headers=headers, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()

        # Extract messages from the session data
        # OCS returns the full session object with messages embedded
        messages = data.get("messages", [])

        return {
            "session_id": session_id,
            "messages": messages,
            "status": data.get("status"),
            "metadata": data,
        }
    except requests.exceptions.RequestException as e:
        logger.error(f"OCS API error when fetching transcript for session {session_id}: {e}")
        raise OCSClientError(f"Failed to fetch transcript: {str(e)}")


def get_recent_session(experiment_id, identifier=None, limit=1):
    """
    Get recent session(s) for an experiment, optionally filtered by participant identifier.

    Args:
        experiment_id: OCS experiment UUID
        identifier: Optional participant identifier to filter by
        limit: Maximum number of sessions to return (default: 1)

    Returns:
        list of session dicts, ordered by most recent first

    Raises:
        OCSClientError: If the API call fails
    """
    config = get_ocs_config()

    if not all([config["base_url"], config["api_key"]]):
        raise OCSClientError("OCS configuration is incomplete. Check OCS_BASE_URL and OCS_API_KEY settings.")

    url = f"{config['base_url']}/api/sessions/"
    headers = {
        "X-Api-Key": config["api_key"],
    }

    params = {
        "experiment": experiment_id,
        "ordering": "-created_at",  # Most recent first
        "page_size": limit,
    }

    try:
        response = requests.get(url, headers=headers, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()

        sessions = data.get("results", [])

        # If identifier provided, filter to matching participant (case-insensitive)
        if identifier and sessions:
            identifier_lower = identifier.lower()
            sessions = [
                s for s in sessions if s.get("participant", {}).get("identifier", "").lower() == identifier_lower
            ]

        return sessions

    except requests.exceptions.RequestException as e:
        logger.error(f"OCS API error when fetching sessions for experiment {experiment_id}: {e}")
        raise OCSClientError(f"Failed to fetch sessions: {str(e)}")


def get_session_status(session_id):
    """
    Get the current status of an OCS session.

    Args:
        session_id: OCS session ID (external UUID)

    Returns:
        dict with status information

    Raises:
        OCSClientError: If the API call fails
    """
    config = get_ocs_config()

    if not all([config["base_url"], config["api_key"]]):
        raise OCSClientError("OCS configuration is incomplete. Check OCS_BASE_URL and OCS_API_KEY settings.")

    # Note: You might want to use the main session endpoint instead
    # as it includes status: GET /api/sessions/{session_id}
    url = f"{config['base_url']}/api/sessions/{session_id}/status"
    headers = {
        "X-Api-Key": config["api_key"],
    }

    try:
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        data = response.json()

        return {
            "session_id": session_id,
            "status": data.get("status"),
            "is_active": data.get("is_active", False),
            "metadata": data,
        }
    except requests.exceptions.RequestException as e:
        logger.error(f"OCS API error when checking session status for {session_id}: {e}")
        raise OCSClientError(f"Failed to get session status: {str(e)}")
