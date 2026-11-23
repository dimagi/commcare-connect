from django import template

from commcare_connect.solicitation.models import SolicitationApplication

register = template.Library()


@register.filter
def has_applications(organization):
    """Whether the organization has applied to any solicitation, drafts included."""
    return SolicitationApplication.objects.filter(organization=organization).exists()
