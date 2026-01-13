import logging

from django.apps import AppConfig
from django.conf import settings

logger = logging.getLogger(__name__)


class LabsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "commcare_connect.labs"
    verbose_name = "Labs"

    def ready(self):
        """Validate labs configuration on startup."""
        # Only validate if we're in labs environment
        if not getattr(settings, "IS_LABS_ENVIRONMENT", False):
            return

        # Check required OAuth settings
        if not getattr(settings, "CONNECT_OAUTH_CLIENT_ID", None):
            logger.error("CONNECT_OAUTH_CLIENT_ID not configured for labs environment")

        if not getattr(settings, "CONNECT_OAUTH_CLIENT_SECRET", None):
            logger.error("CONNECT_OAUTH_CLIENT_SECRET not configured for labs environment")

        if not getattr(settings, "CONNECT_PRODUCTION_URL", None):
            logger.error("CONNECT_PRODUCTION_URL not configured for labs environment")

        logger.info("Labs OAuth configuration validated")
