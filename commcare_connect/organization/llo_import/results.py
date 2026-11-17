import csv
from datetime import datetime
from pathlib import Path

from commcare_connect.organization.llo_import.planning import ImportPlan, Status
from commcare_connect.organization.llo_import.workbook import CONTACTS_TAB, ORGANIZATIONS_TAB

RESULT_COLUMNS = ("Tab", "Row", "Name", "Email", "Connect ID", "Status")
STATUS_TEXT = {Status.CREATED: "Created", Status.ALREADY_EXISTED: "Already existed"}


def results_path_for(path: Path, now: datetime) -> Path:
    """Names the results file next to the workbook, stamped with the given time."""
    path = Path(path)
    return path.with_name(f"{path.stem} - imported {now:%Y-%m-%d %H%M}.csv")


def write_results(plan: ImportPlan, output_path: Path) -> None:
    """Writes one line per sheet row that is now in Connect, with its Connect ID and whether it was created."""
    with open(output_path, "w", newline="", encoding="utf-8-sig") as results_file:
        writer = csv.writer(results_file)
        writer.writerow(RESULT_COLUMNS)
        writer.writerows(_contact_lines(plan))
        writer.writerows(_organization_lines(plan))


def _contact_lines(plan: ImportPlan):
    for contact_plan in plan.contacts:
        if contact_plan.status in STATUS_TEXT:
            source = contact_plan.source
            yield (
                CONTACTS_TAB,
                source.row.number,
                source.name,
                source.email,
                contact_plan.contact.contact_id,
                STATUS_TEXT[contact_plan.status],
            )


def _organization_lines(plan: ImportPlan):
    for organization_plan in plan.organizations:
        if organization_plan.status in STATUS_TEXT:
            organization = organization_plan.organization
            source = organization_plan.source
            yield (
                ORGANIZATIONS_TAB,
                source.row.number if source else "",
                source.name if source else organization.name,
                "",
                organization.slug,
                STATUS_TEXT[organization_plan.status],
            )
