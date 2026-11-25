from datetime import timedelta

import pytest
from django.utils.timezone import now
from waffle.testutils import override_switch

from commcare_connect.flags.switch_names import SOLICITATIONS
from commcare_connect.program.tests.factories import _get_default_currency
from commcare_connect.solicitation.models import Solicitation


def deadline(days=30):
    return (now() + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M")


@pytest.fixture
def draft_data(db):
    return {
        "title": "Malaria outreach",
        "type": Solicitation.Type.RFP,
        "currency": _get_default_currency().pk,
        "application_deadline": deadline(),
    }


@pytest.fixture
def pm_client(client, program_manager_org_user_admin):
    client.force_login(program_manager_org_user_admin)
    with override_switch(SOLICITATIONS, active=True):
        yield client
