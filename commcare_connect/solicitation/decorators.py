from functools import wraps

from django.http import Http404
from waffle import switch_is_active

from commcare_connect.flags.switch_names import SOLICITATIONS


def solicitations_required(view_func):
    """404 the view while the solicitations switch is off."""

    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        _check_solicitations_enabled()
        return view_func(request, *args, **kwargs)

    return wrapper


class SolicitationsRequiredMixin:
    """404 the view while the solicitations switch is off.

    List it first among the view's bases so the feature stays hidden before
    any login or permission mixin gets a chance to redirect.
    """

    def dispatch(self, request, *args, **kwargs):
        _check_solicitations_enabled()
        return super().dispatch(request, *args, **kwargs)


def _check_solicitations_enabled():
    if not switch_is_active(SOLICITATIONS):
        raise Http404
