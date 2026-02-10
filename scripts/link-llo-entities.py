import logging

from django.db import transaction

from commcare_connect.opportunity.models import Opportunity
from commcare_connect.organization.models import LLOEntity, Organization

INPUT_DATA = {}  # Format opp_id: llo_name
LLO_ENTITY_NAMES = set()  # Set of all unique LLO names from the input data


logger = logging.getLogger(__name__)


def create_llo_entities():
    """
    Create LLOEntity records for any LLO names in LLO_ENTITY_NAMES that don't already exist
    """
    names = {name.strip() for name in LLO_ENTITY_NAMES}
    existing = LLOEntity.objects.filter(name__in=names).values_list("name", flat=True)
    missing_names = names - set(existing)
    LLOEntity.objects.bulk_create([LLOEntity(name=name) for name in missing_names])
    return {e.name: e for e in LLOEntity.objects.filter(name__in=names)}


def link_orgs_to_llo_entities(llo_entity_by_name):
    """
    Link organizations to LLOEntity instances based on input data.

    :param llo_entity_by_name: Dictionary mapping LLO names to LLOEntity instances
    """
    opp_ids = set(INPUT_DATA.keys())

    opps = Opportunity.objects.filter(id__in=opp_ids).select_related("organization")
    found_ids = set(opps.values_list("id", flat=True))
    missing_ids = sorted(opp_ids - found_ids)
    if missing_ids:
        logger.warning("Missing Opportunity IDs (not found in DB): %s", missing_ids)

    orgs_to_update = set()
    for opp in opps:
        org = opp.organization
        llo_name = INPUT_DATA[opp.id]
        org.llo_entity = llo_entity_by_name[llo_name]
        orgs_to_update.add(org)

    Organization.objects.bulk_update(orgs_to_update, ["llo_entity"])


with transaction.atomic():
    llo_entity_by_name = create_llo_entities()
    link_orgs_to_llo_entities(llo_entity_by_name)
