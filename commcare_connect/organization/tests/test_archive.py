from datetime import UTC, datetime
from unittest import mock

import pytest
from django.db import DatabaseError

from commcare_connect.organization.archive import archive_organization
from commcare_connect.organization.models import Contact, Organization
from commcare_connect.users.tests.factories import ContactFactory, OrganizationFactory

STAFF_EMAIL = "staff@example.com"


@pytest.mark.django_db
class TestArchiveOrganization:
    def test_archives_the_organization_and_its_active_contacts(self, organization):
        active = ContactFactory(organization=organization)
        already_archived = ContactFactory(organization=organization, is_archived=True)
        other_organization_contact = ContactFactory()

        archive_organization(organization, user_email=STAFF_EMAIL, reason="")

        organization.refresh_from_db()
        assert organization.is_archived
        assert organization.modified_by == STAFF_EMAIL
        active = Contact.objects.get(pk=active.pk)
        assert active.is_archived
        assert active.modified_by == STAFF_EMAIL
        assert Contact.objects.get(pk=already_archived.pk).is_archived
        assert not Contact.objects.get(pk=other_organization_contact.pk).is_archived

    @pytest.mark.parametrize(
        "notes, expected",
        [
            ("", "[System Note] Archived on 2026-10-27 by staff@example.com: Duplicate"),
            ("Existing", "Existing\n[System Note] Archived on 2026-10-27 by staff@example.com: Duplicate"),
        ],
    )
    @mock.patch("commcare_connect.organization.archive.timezone.now", return_value=datetime(2026, 10, 27, tzinfo=UTC))
    def test_reason_is_appended_as_a_system_note(self, _now, notes, expected):
        organization = OrganizationFactory(notes=notes)

        archive_organization(organization, user_email=STAFF_EMAIL, reason="Duplicate")

        organization.refresh_from_db()
        assert organization.notes == expected

    def test_no_reason_leaves_notes_unchanged(self):
        organization = OrganizationFactory(notes="Existing")

        archive_organization(organization, user_email=STAFF_EMAIL, reason="  ")

        organization.refresh_from_db()
        assert organization.notes == "Existing"

    def test_a_failed_save_leaves_the_contacts_unarchived(self, organization):
        contact = ContactFactory(organization=organization)

        with mock.patch.object(Organization, "save", side_effect=DatabaseError), pytest.raises(DatabaseError):
            archive_organization(organization, user_email=STAFF_EMAIL, reason="")

        assert not Contact.objects.get(pk=contact.pk).is_archived

    def test_records_an_update_event(self, organization):
        archive_organization(organization, user_email=STAFF_EMAIL, reason="")

        assert organization.events.latest("pgh_id").is_archived
