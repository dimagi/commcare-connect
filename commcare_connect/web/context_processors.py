from django.conf import settings

from commcare_connect.utils.tables import DEFAULT_PAGE_SIZE, PAGE_SIZE_OPTIONS


def page_settings(request):
    """Expose global page size settings to templates."""
    return {"PAGE_SIZE_OPTIONS": PAGE_SIZE_OPTIONS, "DEFAULT_PAGE_SIZE": DEFAULT_PAGE_SIZE}


def gtm_context(request):
    """Provide Google Tag Manager context variables to templates."""
    return {
        "GTM_VARS_JSON": {
            "userEmail": request.user.email if request.user.is_authenticated else "",
            "gtmID": settings.GTM_ID,
        }
    }
