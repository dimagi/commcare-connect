import pytest

from commcare_connect.users.tests.factories import LLOEntityFactory, UserFactory


@pytest.mark.django_db
class TestLLOEntityMembership:
    def test_members_reverse_accessor(self):
        user = UserFactory()
        entity = LLOEntityFactory()
        entity.members.add(user)
        assert list(user.llo_entities.all()) == [entity]

    def test_remove_member(self):
        user = UserFactory()
        entity = LLOEntityFactory()
        entity.members.add(user)
        entity.members.remove(user)
        assert not user.llo_entities.exists()

    def test_defaults(self):
        entity = LLOEntityFactory()
        assert entity.verified is False
        assert entity.has_used_connect is False
        assert entity.primary_sectors == []
        assert list(entity.countries.all()) == []
        assert list(entity.members.all()) == []
