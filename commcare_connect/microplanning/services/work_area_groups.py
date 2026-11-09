from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _

from commcare_connect.microplanning.models import WorkArea, WorkAreaGroup
from commcare_connect.opportunity.models import Opportunity


def clear_work_area_groups_for_opportunity(opportunity: Opportunity) -> None:
    """Delete the opportunity's work area groups. Raises ValidationError while any work area is assigned."""
    if WorkArea.objects.filter(opportunity_id=opportunity.id, opportunity_access__isnull=False).exists():
        raise ValidationError(_("Work Areas already assigned to users. Work Area Groups cannot be cleared."))
    WorkAreaGroup.objects.filter(opportunity_id=opportunity.id).delete()
