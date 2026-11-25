from io import StringIO

import pytest
from django.core.management import call_command


@pytest.mark.django_db
def test_no_pending_migrations():
    out = StringIO()
    try:
        call_command("makemigrations", check_changes=True, dry_run=True, stdout=out, stderr=StringIO())
    except SystemExit:
        raise AssertionError("Model changes without a migration:\n" + out.getvalue()) from None
