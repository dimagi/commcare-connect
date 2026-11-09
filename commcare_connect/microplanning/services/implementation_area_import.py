import uuid
from functools import partial
from typing import TextIO

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import UploadedFile
from django.db import transaction
from django.utils.translation import gettext as _

from commcare_connect.microplanning.exceptions import AreaCSVColumnsError
from commcare_connect.microplanning.models import ImplementationArea, WorkArea
from commcare_connect.microplanning.utils import parse_implementation_area_csv
from commcare_connect.opportunity.models import Opportunity
from commcare_connect.utils.itertools import batched

INSERT_BATCH_SIZE = 5000
IMPORT_LOCK_TIMEOUT = 1200


def start_implementation_area_import(opportunity: Opportunity, csv_file: UploadedFile) -> str:
    """Store an uploaded implementation area CSV, queue its import after commit and return the task id.

    Raises ValidationError when the upload isn't accepted.
    """
    from commcare_connect.microplanning.tasks import import_implementation_areas_task

    check_implementation_area_upload_can_start(opportunity)
    file_name = f"implementation_area_upload-{opportunity.id}-{uuid.uuid4().hex}.csv"
    default_storage.save(file_name, ContentFile(csv_file.read()))
    task_id = str(uuid.uuid4())
    cache.set(get_implementation_area_import_cache_key(opportunity.id), task_id, timeout=IMPORT_LOCK_TIMEOUT)
    transaction.on_commit(
        partial(import_implementation_areas_task.apply_async, args=[opportunity.id, file_name], task_id=task_id)
    )
    return task_id


def check_implementation_area_upload_can_start(opportunity: Opportunity) -> None:
    """Raise ValidationError if the opportunity has implementation areas or an import is running."""
    if ImplementationArea.objects.filter(opportunity_id=opportunity.id).exists():
        raise ValidationError(_("Implementation Areas already exist for this opportunity."))
    if cache.get(get_implementation_area_import_cache_key(opportunity.id)):
        raise ValidationError(_("An import for this opportunity is already in progress."))


def get_implementation_area_import_cache_key(opp_id: int) -> str:
    return f"implementation_area_import_lock_{opp_id}"


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
