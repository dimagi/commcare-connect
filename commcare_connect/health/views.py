import hmac

from django.conf import settings
from django.db import transaction
from django.http import Http404, HttpResponse
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from health_check.views import HealthCheckView

from commcare_connect.health.checks import get_check_subsets


@never_cache
@transaction.non_atomic_requests
def up(request):
    """Liveness probe for container and load balancer health checks.

    Only shows that Django is serving requests: it touches no database or other service (hence
    ``non_atomic_requests``, since ``ATOMIC_REQUESTS`` would otherwise open a connection), so an
    outage elsewhere does not get healthy containers restarted. Use /status/ for dependencies.

    Also served at /export/up, so that a monitor going through the load balancer reaches the
    pool that serves the /export/ API.
    """
    return HttpResponse("OK", content_type="text/plain")


class HealthCheck(HealthCheckView):
    """Dependency health for the status page: /status/ and /status/<subset>/.

    When ``HEALTH_CHECK_TOKENS`` is set, a request must pass one of them, either in an
    ``Authorization: Bearer <token>`` header or as ``?token=``. A missing or wrong token, like an
    unknown subset, gets a 404 so the endpoint does not reveal itself.
    """

    # Restated from HealthCheckView: overriding dispatch drops the parent's decorator, and Django
    # refuses to wrap an async view in ATOMIC_REQUESTS.
    @method_decorator(transaction.non_atomic_requests)
    async def dispatch(self, request, *args, subset="general", **kwargs):
        subsets = get_check_subsets()
        if not has_valid_token(request) or subset not in subsets:
            raise Http404
        self.checks = subsets[subset]
        return await super().dispatch(request, *args, **kwargs)


def has_valid_token(request):
    tokens = settings.HEALTH_CHECK_TOKENS
    if not tokens:
        return True
    scheme, _, header_token = request.headers.get("Authorization", "").partition(" ")
    given = [request.GET.get("token", "")]
    if scheme.lower() == "bearer":
        given.append(header_token.strip())
    # Compare against every token, without stopping early, so timing does not reveal a match.
    matches = [hmac.compare_digest(value.encode(), token.encode()) for value in given if value for token in tokens]
    return any(matches)
