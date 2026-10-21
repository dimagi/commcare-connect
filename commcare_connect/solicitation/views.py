from django.db.models import Count, Q

from commcare_connect.solicitation.models import Solicitation, SolicitationApplication

# Drafts haven't been sent yet and withdrawn applications were taken back, so neither is a response.
NON_RESPONSE_STATUSES = (SolicitationApplication.Status.DRAFT, SolicitationApplication.Status.WITHDRAWN)


def org_solicitations(organization):
    return (
        Solicitation.objects.filter(organization=organization)
        .annotate(response_count=Count("applications", filter=~Q(applications__status__in=NON_RESPONSE_STATUSES)))
        .order_by("-date_created")
    )
