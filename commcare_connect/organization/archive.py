from django.db import transaction
from django.utils import timezone

from commcare_connect.organization.models import Organization


@transaction.atomic
def archive_organization(organization: Organization, *, user_email: str, reason: str) -> None:
    """Archive the organization together with its contacts, noting the reason when one is given."""
    organization.contacts.filter(is_archived=False).update(
        is_archived=True, modified_by=user_email, date_modified=timezone.now()
    )
    organization.is_archived = True
    organization.modified_by = user_email
    _append_system_note(organization, "Archived", user_email, reason)
    organization.save(update_fields=["is_archived", "notes", "modified_by", "date_modified"])


def _append_system_note(organization: Organization, action: str, user_email: str, reason: str) -> None:
    reason = reason.strip()
    if not reason:
        return
    note = f"[System Note] {action} on {timezone.now().date():%Y-%m-%d} by {user_email}: {reason}"
    organization.notes = f"{organization.notes}\n{note}" if organization.notes else note
