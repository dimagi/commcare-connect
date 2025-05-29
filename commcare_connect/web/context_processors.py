from commcare_connect.utils.tables import PAGE_SIZE_OPTIONS, DEFAULT_PAGE_SIZE


def page_settings(request):
    """Expose global page size settings to templates."""
    return {
        "PAGE_SIZE_OPTIONS": PAGE_SIZE_OPTIONS,
        "DEFAULT_PAGE_SIZE": DEFAULT_PAGE_SIZE,
    }
