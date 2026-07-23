import pytest

from commcare_connect.organization.forms import LLOEntityForm
from commcare_connect.users.tests.factories import LLOEntityFactory


def _valid_data(**overrides):
    data = {
        "name": "Test Entity",
        "short_name": "TE",
        "has_used_connect": False,
        "year_of_establishment": 2010,
        "team_size": 25,
        "flws_managed": 100,
        "regions": "Maharashtra\nGujarat",
        "primary_sectors": ["other"],
        "website": "https://example.org",
        "office_address": "123 Lane",
        "contact_emails": "a@example.com\nb@example.com",
        "eoi_links": "https://eoi.example.org/1",
        "notes": "",
    }
    data.update(overrides)
    return data


@pytest.mark.django_db
class TestLLOEntityForm:
    def test_valid(self):
        form = LLOEntityForm(data=_valid_data())
        assert form.is_valid(), form.errors

    def test_verified_field_excluded(self):
        form = LLOEntityForm(data=_valid_data())
        assert "verified" not in form.fields
        assert "members" not in form.fields

    def test_posting_verified_has_no_effect(self):
        form = LLOEntityForm(data=_valid_data(verified=True))
        assert form.is_valid(), form.errors
        entity = form.save()
        assert entity.verified is False

    @pytest.mark.parametrize(
        "emails,valid",
        [
            ("a@example.com", True),
            ("a@example.com\nb@example.com", True),
            ("a@example.com\n   \nb@example.com", True),
            ("not-an-email", False),
            ("a@example.com\nnot-an-email", False),
        ],
    )
    def test_contact_emails(self, emails, valid):
        form = LLOEntityForm(data=_valid_data(contact_emails=emails))
        assert form.is_valid() == valid

    @pytest.mark.parametrize(
        "links,valid",
        [
            ("https://x.org", True),
            ("https://x.org\nhttps://y.org", True),
            ("not-a-url", False),
            ("https://x.org\nnot-a-url", False),
        ],
    )
    def test_eoi_links(self, links, valid):
        form = LLOEntityForm(data=_valid_data(eoi_links=links))
        assert form.is_valid() == valid

    @pytest.mark.parametrize(
        "year,valid",
        [
            (1799, False),
            (1800, True),
            (2024, True),
            (9999, False),
        ],
    )
    def test_year_of_establishment_bounds(self, year, valid):
        form = LLOEntityForm(data=_valid_data(year_of_establishment=year))
        assert form.is_valid() == valid

    def test_unique_name(self):
        LLOEntityFactory(name="Duplicate")
        form = LLOEntityForm(data=_valid_data(name="Duplicate"))
        assert not form.is_valid()
        assert "name" in form.errors
