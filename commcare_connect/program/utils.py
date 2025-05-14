def is_program_manager(request):
    return request.org.program_manager and (
        (request.org_membership != None and request.org_membership.is_admin) or request.user.is_superuser  # noqa: E711
    )


def is_program_manager_of_opportunity(request, opportunity):
    return (
        opportunity.managed
        and opportunity.managedopportunity.program.organization.slug == request.org.slug
        and is_program_manager(request)
    )
