import datetime

from commcare_connect.prelogin.external_coverage import get_entries
from commcare_connect.prelogin.templatetags.prelogin_extras import for_program


def test_get_entries_sorts_newest_first():
    entries = get_entries()
    dates = [entry["date"] for entry in entries]
    assert dates == sorted(dates, reverse=True)


def test_get_entries_every_entry_has_the_required_fields():
    required = {"slug", "outlet", "date", "date_display", "title", "authors", "label", "summary", "url"}
    for entry in get_entries():
        missing = required - entry.keys()
        assert not missing, f"{entry.get('slug')} is missing {missing}"
        assert isinstance(entry["date"], datetime.date)


def test_for_program_filters_to_matching_entries():
    entries = [
        {"slug": "a", "programs": ["reading-glasses"]},
        {"slug": "b", "programs": ["kangaroo-mother-care"]},
        {"slug": "c", "programs": ["reading-glasses", "kangaroo-mother-care"]},
    ]
    assert [e["slug"] for e in for_program(entries, "reading-glasses")] == ["a", "c"]


def test_for_program_with_no_program_key_returns_everything_unfiltered():
    entries = [{"slug": "a", "programs": ["reading-glasses"]}, {"slug": "b"}]
    assert for_program(entries, "") == entries
    assert for_program(entries, None) == entries


def test_for_program_entry_with_no_programs_field_never_matches():
    assert for_program([{"slug": "a"}], "reading-glasses") == []
