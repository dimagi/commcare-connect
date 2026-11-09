from commcare_connect.microplanning.models import ImplementationArea
from commcare_connect.opportunity.models import Opportunity


def clear_implementation_areas_for_opportunity(opportunity: Opportunity) -> None:
    """Delete the opportunity's implementation areas so a fresh set can be uploaded.

    Work areas keep their implementation_area_name (the FK is set null via SET_NULL) and re-link on
    the next implementation area upload.
    """
    ImplementationArea.objects.filter(opportunity_id=opportunity.id).delete()
