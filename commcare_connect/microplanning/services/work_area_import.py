import math
from collections import defaultdict
from typing import TextIO

from django.db import transaction
from django.utils.translation import gettext as _

from commcare_connect.microplanning.const import WORK_AREA_OVERLAP_ERROR_LIMIT, WORK_AREA_OVERLAP_RATIO_TOLERANCE
from commcare_connect.microplanning.dataclasses import WorkAreaRow
from commcare_connect.microplanning.exceptions import AreaCSVColumnsError
from commcare_connect.microplanning.models import ImplementationArea, WorkArea, WorkAreaGroup
from commcare_connect.microplanning.overlap import Overlap, find_overlapping_work_areas
from commcare_connect.microplanning.utils import parse_work_area_csv
from commcare_connect.opportunity.models import Opportunity
from commcare_connect.utils.itertools import batched

INSERT_BATCH_SIZE = 5000


class _ImportRejected(Exception):
    """Raised inside the import's transaction to undo everything saved so far."""


def import_uploaded_work_areas(opportunity_id: int, csv_source: TextIO) -> dict:
    """Import an uploaded work area CSV, which is only accepted while the opportunity has none."""
    if WorkArea.objects.filter(opportunity_id=opportunity_id).exists():
        return {"errors": {_("Work Areas already exist for this opportunity"): [0]}}
    return import_work_areas(opportunity_id=opportunity_id, csv_source=csv_source)


def import_work_areas(opportunity_id: int, csv_source: TextIO) -> dict:
    """Check a work area CSV against the opportunity's saved data and save its rows.

    Returns ``{"created": count}``, or ``{"errors": {message: [line numbers]}}`` and saves nothing.
    The rows are saved in one transaction; under ATOMIC_REQUESTS that is a savepoint, so a rejected
    file undoes only the import's own work.
    """
    try:
        parsed = parse_work_area_csv(csv_source)
    except AreaCSVColumnsError as e:
        return {"errors": {e.message: [1]}}

    errors = parsed.errors
    rows = parsed.rows
    try:
        with transaction.atomic():
            # Lock the opportunity so the checks below can't go stale before the insert.
            Opportunity.objects.select_for_update(no_key=True).get(pk=opportunity_id)
            existing_groups = _check_against_saved_work_areas(opportunity_id=opportunity_id, rows=rows, errors=errors)
            if errors:
                raise _ImportRejected

            has_group_names = bool(rows) and all(row.group_name for row in rows)
            groups = (
                _get_or_create_groups(opportunity_id=opportunity_id, rows=rows, existing_groups=existing_groups)
                if has_group_names
                else {}
            )
            inserted = _insert_work_areas(opportunity_id=opportunity_id, rows=rows, groups=groups)
            _check_overlaps(inserted=inserted, errors=errors)
            if errors:
                raise _ImportRejected

            for group in groups.values():
                group.update_centroid(commit=False)
            WorkAreaGroup.objects.bulk_update(groups.values(), ["centroid"])
    except _ImportRejected:
        return {"errors": errors}
    return {"created": len(inserted)}


def _check_against_saved_work_areas(
    opportunity_id: int, rows: list[WorkAreaRow], errors: dict[str, list[int]]
) -> dict[str, WorkAreaGroup]:
    """Report slugs already in use and rows whose ward doesn't match their group's.

    Returns the saved groups the file names, by name.
    """
    slug_lines = {}
    for row in rows:
        if row.slug:
            slug_lines.setdefault(row.slug, row.line_num)
    existing_slugs = WorkArea.objects.filter(opportunity_id=opportunity_id, slug__in=slug_lines).values_list(
        "slug", flat=True
    )
    for line_num in sorted(slug_lines[slug] for slug in existing_slugs):
        errors[_("Area slug already exists for this opportunity")].append(line_num)

    return _check_group_wards(opportunity_id=opportunity_id, rows=rows, errors=errors)


def _check_group_wards(
    opportunity_id: int, rows: list[WorkAreaRow], errors: dict[str, list[int]]
) -> dict[str, WorkAreaGroup]:
    """Every row of a group must give the group's ward: its saved ward, or for a new group the
    ward of its first row, spelled the same way."""
    group_rows = defaultdict(list)
    for row in rows:
        # A missing ward is already its own row error.
        if row.group_name and row.ward:
            group_rows[row.group_name].append(row)
    if not group_rows:
        return {}

    existing_groups = {
        group.name: group for group in WorkAreaGroup.objects.filter(opportunity_id=opportunity_id, name__in=group_rows)
    }
    for group_name, rows_in_group in group_rows.items():
        group = existing_groups.get(group_name)
        group_ward = group.ward if group else rows_in_group[0].ward
        for row in rows_in_group:
            if row.ward != group_ward:
                message = _(
                    "Work Area Group %(group)s is in ward %(group_ward)s, but these rows give ward %(ward)s. "
                    "Correct the ward, or use a different group name."
                ) % {"group": group_name, "group_ward": group_ward, "ward": row.ward}
                errors[message].append(row.line_num)
    return existing_groups


def _get_or_create_groups(
    opportunity_id: int, rows: list[WorkAreaRow], existing_groups: dict[str, WorkAreaGroup]
) -> dict[str, WorkAreaGroup]:
    groups = dict(existing_groups)
    # A new group takes the ward of its first row, as that row spelled it.
    new_group_wards = {}
    for row in rows:
        if row.group_name not in groups:
            new_group_wards.setdefault(row.group_name, row.ward)
    if new_group_wards:
        created = WorkAreaGroup.objects.bulk_create(
            [
                WorkAreaGroup(opportunity_id=opportunity_id, name=name, ward=ward)
                for name, ward in new_group_wards.items()
            ]
        )
        groups.update({group.name: group for group in created})
    return groups


def _insert_work_areas(
    opportunity_id: int, rows: list[WorkAreaRow], groups: dict[str, WorkAreaGroup]
) -> dict[int, tuple[int, WorkArea]]:
    """Save the rows and return pk -> (CSV line number, saved work area)."""
    implementation_area_ids = dict(
        ImplementationArea.objects.filter(opportunity_id=opportunity_id).values_list("name", "id")
    )
    inserted = {}
    for batch in batched(rows, INSERT_BATCH_SIZE):
        # On PostgreSQL bulk_create sets the primary keys on the instances it returns.
        created = WorkArea.objects.bulk_create(
            [
                WorkArea(
                    opportunity_id=opportunity_id,
                    slug=row.slug,
                    ward=row.ward,
                    centroid=row.centroid,
                    boundary=row.boundary,
                    building_count=row.building_count,
                    expected_visit_count=row.expected_visit_count,
                    target_population=row.target_population,
                    work_area_group=groups.get(row.group_name),
                    implementation_area_id=implementation_area_ids.get(row.implementation_area_name),
                    implementation_area_name=row.implementation_area_name,
                    case_properties={"lga": row.lga, "state": row.state},
                )
                for row in batch
            ]
        )
        inserted.update((work_area.pk, (row.line_num, work_area)) for row, work_area in zip(batch, created))
    return inserted


def _check_overlaps(inserted: dict[int, tuple[int, WorkArea]], errors: dict[str, list[int]]) -> None:
    """Report saved work areas that overlap each other or an existing one."""
    if not inserted:
        return
    overlaps = find_overlapping_work_areas(list(inserted))
    # One extra row tells whether there are more than the limit, without counting every pair.
    listed = list(overlaps[: WORK_AREA_OVERLAP_ERROR_LIMIT + 1])
    for overlap in listed[:WORK_AREA_OVERLAP_ERROR_LIMIT]:
        _add_overlap_error(overlap=overlap, inserted=inserted, errors=errors)
    if len(listed) > WORK_AREA_OVERLAP_ERROR_LIMIT:
        hidden = overlaps.count() - WORK_AREA_OVERLAP_ERROR_LIMIT
        errors[_("%(count)d more overlaps not shown.") % {"count": hidden}].append(1)


def _add_overlap_error(
    overlap: Overlap, inserted: dict[int, tuple[int, WorkArea]], errors: dict[str, list[int]]
) -> None:
    # Messages name work areas by slug: API callers get only the message, and their "rows" are
    # lines of a CSV built from the JSON list. The upload page shows the rows beside it.
    line_num, work_area = inserted[overlap["pk"]]
    values = {
        "slug": work_area.slug,
        "other_slug": overlap["other_slug"],
        "percent": _overlap_percent(overlap["ratio"]),
    }
    other = inserted.get(overlap["other_id"])
    if other is None:
        message = _(
            "Work Area %(slug)s overlaps existing Work Area %(other_slug)s by %(percent)s%%. "
            "Correct the boundary or remove the work area, then upload again."
        )
        line_nums = [line_num]
    else:
        message = _(
            "Work Areas %(slug)s and %(other_slug)s overlap each other by %(percent)s%%. "
            "Correct one boundary or remove one work area, then upload again."
        )
        line_nums = [line_num, other[0]]
    for line in line_nums:
        errors[message % values].append(line)


def _overlap_percent(ratio: float) -> str:
    """The overlap as a percentage, rounded up to one decimal so a pair just over the limit never
    reads as exactly at it (5.04% shows as 5.1%, not 5.0%). Floating-point noise in an exact ratio
    (0.06000000000002) is ignored with the same tolerance as the limit, so it doesn't round up a
    whole step, and every rejected pair reads as more than the limit."""
    return f"{math.ceil((ratio - WORK_AREA_OVERLAP_RATIO_TOLERANCE) * 1000) / 10:.1f}"
