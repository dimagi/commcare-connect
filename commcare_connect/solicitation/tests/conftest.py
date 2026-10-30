from datetime import timedelta

import pytest
from django.utils.timezone import now

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
