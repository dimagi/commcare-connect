from unittest.mock import MagicMock, patch

import pytest
import requests

from commcare_connect.tasks.ocs_client import OCSClientError, get_transcript, trigger_bot


@pytest.fixture
def mock_settings():
    """Mock OCS settings."""
    with patch("commcare_connect.tasks.ocs_client.settings") as mock_settings:
        mock_settings.OCS_BASE_URL = "https://ocs.example.com"
        mock_settings.OCS_API_KEY = "test-api-key"
        mock_settings.OCS_BOT_ID = "test-bot-id"
        yield mock_settings


@pytest.mark.django_db
class TestOCSClient:
    @patch("commcare_connect.tasks.ocs_client.requests.post")
    def test_trigger_bot_success(self, mock_post, mock_settings):
        """Test successful bot trigger."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "session_id": "session-123",
            "status": "initiated",
        }
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        result = trigger_bot(task_id=1, flw_phone="+1234567890", context={"task_type": "warning"})

        assert result["session_id"] == "session-123"
        assert result["status"] == "initiated"

        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert "https://ocs.example.com/api/trigger_bot" in call_args[0]
        assert call_args[1]["json"]["bot_id"] == "test-bot-id"
        assert call_args[1]["json"]["recipient"] == "+1234567890"

    @patch("commcare_connect.tasks.ocs_client.requests.post")
    def test_trigger_bot_api_error(self, mock_post, mock_settings):
        """Test bot trigger handles API errors."""
        mock_post.side_effect = requests.exceptions.RequestException("API Error")

        with pytest.raises(OCSClientError) as exc_info:
            trigger_bot(task_id=1, flw_phone="+1234567890")

        assert "Failed to trigger bot" in str(exc_info.value)

    def test_trigger_bot_missing_config(self):
        """Test bot trigger fails with missing config."""
        with patch("commcare_connect.tasks.ocs_client.settings") as mock_settings:
            mock_settings.OCS_BASE_URL = ""
            mock_settings.OCS_API_KEY = ""
            mock_settings.OCS_BOT_ID = ""

            with pytest.raises(OCSClientError) as exc_info:
                trigger_bot(task_id=1, flw_phone="+1234567890")

            assert "configuration is incomplete" in str(exc_info.value)

    @patch("commcare_connect.tasks.ocs_client.requests.get")
    def test_get_transcript_success(self, mock_get, mock_settings):
        """Test successful transcript fetch."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "session_id": "session-123",
            "messages": [
                {"actor": "bot", "text": "Hello"},
                {"actor": "user", "text": "Hi"},
            ],
            "status": "completed",
        }
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        result = get_transcript("session-123")

        assert result["session_id"] == "session-123"
        assert len(result["messages"]) == 2
        assert result["status"] == "completed"

        mock_get.assert_called_once()
        call_args = mock_get.call_args
        assert "session-123" in call_args[0][0]

    @patch("commcare_connect.tasks.ocs_client.requests.get")
    def test_get_transcript_api_error(self, mock_get, mock_settings):
        """Test transcript fetch handles API errors."""
        mock_get.side_effect = requests.exceptions.RequestException("API Error")

        with pytest.raises(OCSClientError) as exc_info:
            get_transcript("session-123")

        assert "Failed to fetch transcript" in str(exc_info.value)

    def test_get_transcript_missing_config(self):
        """Test transcript fetch fails with missing config."""
        with patch("commcare_connect.tasks.ocs_client.settings") as mock_settings:
            mock_settings.OCS_BASE_URL = ""
            mock_settings.OCS_API_KEY = ""

            with pytest.raises(OCSClientError) as exc_info:
                get_transcript("session-123")

            assert "configuration is incomplete" in str(exc_info.value)
