from typing import TextIO

from django.db import transaction
from django.utils.translation import gettext as _

from commcare_connect.microplanning.exceptions import AreaCSVColumnsError
from commcare_connect.microplanning.models import ImplementationArea, WorkArea
from commcare_connect.microplanning.utils import parse_implementation_area_csv
from commcare_connect.utils.itertools import batched

INSERT_BATCH_SIZE = 5000


def import_uploaded_implementation_areas(opportunity_id: int, csv_source: TextIO) -> dict:
    """Import an uploaded implementation area CSV, which is only accepted while the opportunity has none."""
    if ImplementationArea.objects.filter(opportunity_id=opportunity_id).exists():
        return {"errors": {_("Implementation Areas already exist for this opportunity"): [0]}}
    return import_implementation_areas(opportunity_id=opportunity_id, csv_source=csv_source)


def import_implementation_areas(opportunity_id: int, csv_source: TextIO) -> dict:
    """Check an implementation area CSV against the opportunity's saved areas and save its rows.

    Returns ``{"created": count}``, or ``{"errors": {message: [line numbers]}}`` and saves nothing.
    Work areas that name a saved implementation area but aren't linked to it yet are linked.
    """
    try:
        parsed = parse_implementation_area_csv(csv_source)
    except AreaCSVColumnsError as e:
        return {"errors": {e.message: [1]}}

    errors = parsed.errors
    with transaction.atomic():
        name_lines = {}
        for row in parsed.rows:
            if row.name:
                name_lines.setdefault(row.name, row.line_num)
        existing_names = ImplementationArea.objects.filter(
            opportunity_id=opportunity_id, name__in=name_lines
        ).values_list("name", flat=True)
        for line_num in sorted(name_lines[name] for name in existing_names):
            errors[_("Implementation Area Name already exists for this opportunity")].append(line_num)
        if errors:
            return {"errors": errors}

        created_count = 0
        for batch in batched(parsed.rows, INSERT_BATCH_SIZE):
            created = ImplementationArea.objects.bulk_create(
                [
                    ImplementationArea(
                        opportunity_id=opportunity_id, name=row.name, centroid=row.centroid, boundary=row.boundary
                    )
                    for row in batch
                ]
            )
            created_count += len(created)

        _link_work_areas(opportunity_id)
    return {"created": created_count}


def _link_work_areas(opportunity_id: int) -> None:
    # Link work areas that were uploaded before their implementation area existed (e.g. after
    # the areas were cleared and re-uploaded): match each unlinked work area by stored name.
    for area in ImplementationArea.objects.filter(opportunity_id=opportunity_id):
        WorkArea.objects.filter(
            opportunity_id=opportunity_id,
            implementation_area__isnull=True,
            implementation_area_name=area.name,
        ).update(implementation_area=area)
