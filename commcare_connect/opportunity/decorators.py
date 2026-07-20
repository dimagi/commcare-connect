from functools import wraps

from django.http import HttpResponseForbidden


def require_manual_visit_verification(view_func):
    """
    Reject requests for opportunities with automatic_visit_verification=True.

    The endpoint is only meaningful for manual-review opportunities. Must be
    applied after @opportunity_required so request.opportunity is set.
    Returns 403 with an HX-Trigger: reload_table header so HTMX clients
    refresh after a guard hit instead of showing a stale UI.
    """

    @wraps(view_func)
    def _inner(request, *args, **kwargs):
        assert getattr(request, "opportunity", None) is not None, (
            "require_manual_visit_verification must be applied after @opportunity_required"
        )
        if request.opportunity.automatic_visit_verification:
            response = HttpResponseForbidden()
            response["HX-Trigger"] = "reload_table"
            return response
        return view_func(request, *args, **kwargs)

    return _inner
