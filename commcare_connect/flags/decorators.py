from django.http import Http404

from commcare_connect.flags.utils import is_flag_active


def require_flag_for_opp(flag_name: str):
    """Decorator to require a flag to be active for the request organization."""

    def decorator(func):
        def wrapper(request, *args, **kwargs):
            if not is_flag_active(flag_name, request.opportunity):
                raise Http404(f"Flag '{flag_name}' is not active for this organization.")
            return func(request, *args, **kwargs)

        return wrapper

    return decorator
