import pytest
from django.core.exceptions import ValidationError

from commcare_connect.multidb.models import SupersetReplicatedTable


@pytest.fixture
def empty_table(db):
    SupersetReplicatedTable.objects.all().delete()


@pytest.mark.django_db
@pytest.mark.usefixtures("empty_table")
class TestSupersetReplicatedTable:
    def test_db_table_resolves(self):
        entry = SupersetReplicatedTable.objects.create(model_label="opportunity.opportunity")
        assert entry.db_table == "opportunity_opportunity"

    def test_str_returns_model_label(self):
        entry = SupersetReplicatedTable(model_label="users.user")
        assert str(entry) == "users.user"

    @pytest.mark.parametrize(
        "bad_label",
        ["no_dot", "missing.model", "fake_app.fake_model", ""],
    )
    def test_clean_rejects_invalid_label(self, bad_label):
        entry = SupersetReplicatedTable(model_label=bad_label)
        with pytest.raises(ValidationError):
            entry.clean()

    def test_clean_accepts_valid_label(self):
        SupersetReplicatedTable(model_label="opportunity.opportunity").clean()

    def test_model_label_must_be_unique(self):
        SupersetReplicatedTable.objects.create(model_label="opportunity.opportunity")
        with pytest.raises(Exception):
            SupersetReplicatedTable.objects.create(model_label="opportunity.opportunity")
