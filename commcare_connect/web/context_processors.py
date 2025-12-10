from django.conf import settings

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
