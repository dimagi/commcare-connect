from commcare_connect.cache import quickcache
from commcare_connect.program.models import ManagedOpportunity


@quickcache(vary_on=["opp_id"], timeout=60 * 60 * 24)
def get_managed_opp(opp_id) -> ManagedOpportunity | None:
    return ManagedOpportunity.objects.select_related("program__organization").filter(id=opp_id).first()


def is_program_manager(request):
    return request.org.program_manager and (
        (request.org_membership != None and request.org_membership.is_admin) or request.user.is_superuser  # noqa: E711
    )


def is_program_manager_of_opportunity(request, opp_id) -> bool:
    managed_opp = get_managed_opp(opp_id)
    return bool(
        managed_opp
        and managed_opp.managed
        and managed_opp.program.organization.slug == request.org.slug
        and is_program_manager(request)
    )
