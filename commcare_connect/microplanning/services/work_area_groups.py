from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _

from commcare_connect.microplanning.models import WorkArea, WorkAreaGroup
from commcare_connect.opportunity.models import Opportunity


def clear_work_area_groups_for_opportunity(opportunity: Opportunity) -> None:
    """Delete the opportunity's work area groups. Raises ValidationError while any work area is assigned."""
    if WorkArea.objects.filter(opportunity_id=opportunity.id, opportunity_access__isnull=False).exists():
        raise ValidationError(_("Work Areas already assigned to users. Work Area Groups cannot be cleared."))
    WorkAreaGroup.objects.filter(opportunity_id=opportunity.id).delete()


def start_work_area_clustering(opportunity: Opportunity, max_buildings: int) -> str:
    """Queue clustering of the opportunity's work areas into groups and return the task id.

    Raises ValidationError when clustering can't start or is already running.
    """
    from commcare_connect.microplanning.tasks import cluster_work_areas_task

    check_work_area_clustering_can_start(opportunity)
    if cache.lock(get_cluster_area_cache_lock_key(opportunity.id)).locked():
        raise ValidationError(_("Work Area Clustering is already in progress for this opportunity."))

    task = cluster_work_areas_task.delay(opportunity.id, max_buildings)
    return task.id


def check_work_area_clustering_can_start(opportunity: Opportunity) -> None:
    """Raise ValidationError unless the opportunity has work areas and no groups yet."""
    if not WorkArea.objects.filter(opportunity_id=opportunity.id).exists():
        raise ValidationError(_("Please upload Work Areas for this opportunity."))

    if WorkAreaGroup.objects.filter(opportunity_id=opportunity.id).exists():
        raise ValidationError(_("Work Area Groups already exist for this opportunity."))


def get_cluster_area_cache_lock_key(opp_id: int) -> str:
    return f"work_area_clustering_cache_lock_key_{opp_id}"
