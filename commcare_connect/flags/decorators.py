from django.http import Http404

from commcare_connect.flags.models import Flag


def require_flag_for_opp(flag_name: str):
    """Decorator to require a flag to be active for the request organization."""

    def decorator(func):
        def wrapper(request, *args, **kwargs):
            flag, _ = Flag.objects.get_or_create(name=flag_name)
            if not flag.is_active_for(request.opportunity):
                raise Http404(f"Flag '{flag_name}' is not active for this organization.")
            return func(request, *args, **kwargs)

        return wrapper

    return decorator
