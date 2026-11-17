from datetime import UTC, datetime, timedelta

import pytest
from django.utils import timezone


@pytest.mark.django_db
def test_at_moves_the_clock_the_app_reads(sim):
    when = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)

    with sim.at(when):
        inside = timezone.now()

    assert when <= inside < when + timedelta(seconds=5)
    assert timezone.now() > when + timedelta(days=1)
