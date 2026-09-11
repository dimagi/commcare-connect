import pytest
from django.contrib.admin.sites import site
from django.urls import reverse

from commcare_connect.program.models import Program, ProgramFunderEvent
from commcare_connect.utils.admin import UNKNOWN


@pytest.fixture
def funded_program(program, funder_org, watcher_org):
    program.funder = funder_org
    program.save()
    program.watchers.add(watcher_org)
    return program


@pytest.mark.django_db
class TestAuditEventAdmin:
    @pytest.mark.parametrize(
        "url_name",
        [
            "admin:program_programfunderevent_changelist",
            "admin:program_programwatcherevent_changelist",
            "admin:opportunity_opportunitysupervisingorganizationevent_changelist",
        ],
    )
    def test_changelist_renders(self, admin_client, url_name, funded_program, opportunity):
        response = admin_client.get(reverse(url_name))

        assert response.status_code == 200

    def test_audit_rows_cannot_be_edited_or_deleted(self, admin_client, funded_program):
        event = ProgramFunderEvent.objects.filter(funder__isnull=False).first()

        change = admin_client.get(reverse("admin:program_programfunderevent_change", args=[event.pgh_id]))
        add = admin_client.get(reverse("admin:program_programfunderevent_add"))

        assert change.status_code == 200
        assert not change.context["has_change_permission"]
        assert not change.context["has_delete_permission"]
        assert add.status_code == 403

    def test_changed_by_falls_back_when_there_is_no_context(self, funded_program):
        """Trigger-recorded changes made outside a request carry no context."""
        event = ProgramFunderEvent.objects.filter(funder__isnull=False).first()
        model_admin = site._registry[ProgramFunderEvent]

        assert event.pgh_context is None
        assert model_admin.changed_by(event) == UNKNOWN
        assert model_admin.changed_by_email(event) == UNKNOWN

    def test_event_outlives_the_object_it_describes(self, funded_program):
        """The audit row survives deletion, so its pointer has to degrade rather than raise."""
        event = ProgramFunderEvent.objects.filter(funder__isnull=False).first()
        model_admin = site._registry[ProgramFunderEvent]
        Program.objects.filter(pk=funded_program.pk).delete()

        event.refresh_from_db()

        assert ProgramFunderEvent.objects.filter(pgh_id=event.pgh_id).exists()
        assert model_admin.program(event) == UNKNOWN
