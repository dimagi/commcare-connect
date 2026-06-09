from django.conf import settings

from commcare_connect.flags.flag_names import OPEN_CHAT_STUDIO_WIDGET
from commcare_connect.flags.models import Flag
from commcare_connect.utils.tables import DEFAULT_PAGE_SIZE, PAGE_SIZE_OPTIONS


def page_settings(request):
    """Expose global page size settings to templates."""
    return {"PAGE_SIZE_OPTIONS": PAGE_SIZE_OPTIONS, "DEFAULT_PAGE_SIZE": DEFAULT_PAGE_SIZE}


def gtm_context(request):
    """Provide Google Tag Manager context variables to templates."""
    is_dimagi = request.user.is_authenticated and (request.user.email and request.user.email.endswith("@dimagi.com"))
    user_id = request.user.id if request.user.is_authenticated else None
    return {
        "GTM_VARS_JSON": {
            "isDimagi": is_dimagi,
            "gtmID": settings.GTM_ID,
            "userId": user_id,
        }
    }


def chat_widget_context(request):
    creds_configured = bool(settings.CHATBOT_ID and settings.CHATBOT_EMBED_KEY)
    return {
        "chat_widget_enabled": creds_configured and Flag.is_flag_active_for_request(request, OPEN_CHAT_STUDIO_WIDGET),
        "chatbot_id": settings.CHATBOT_ID,
        "chatbot_embed_key": settings.CHATBOT_EMBED_KEY,
    }
