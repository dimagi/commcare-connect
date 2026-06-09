from commcare_connect.flags.models import Flag
from commcare_connect.opportunity.models import Opportunity
from commcare_connect.organization.models import Organization
from commcare_connect.program.models import Program


def is_flag_active(flag_name, obj: Opportunity | Organization | Program):
    flag, _ = Flag.objects.get_or_create(name=flag_name)
    return flag.is_active_for(obj)
