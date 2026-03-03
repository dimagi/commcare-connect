import uuid

import pytest
from django.db import models
from django.http import Http404
from waffle.testutils import override_switch

from commcare_connect.flags.switch_names import API_UUID
from commcare_connect.utils.db import get_object_by_uuid_or_int, get_object_or_list_by_uuid_or_int


class Example(models.Model):
    example_id = models.UUIDField(default=uuid.uuid4, unique=True)

    class Meta:
        app_label = "utils"


@pytest.fixture(scope="class")
def create_example_table(django_db_blocker):
    from django.db import connection

    with django_db_blocker.unblock():
        with connection.schema_editor() as schema_editor:
            schema_editor.create_model(Example)
    yield

    with django_db_blocker.unblock():
        with connection.schema_editor() as schema_editor:
            schema_editor.delete_model(Example)


@pytest.mark.django_db
@pytest.mark.usefixtures("create_example_table")
class TestGetObjectForApiVersion:
    @pytest.mark.parametrize(
        "switch_active,lookup_value",
        [
            (True, lambda obj: str(obj.example_id)),
            (False, lambda obj: str(obj.example_id)),
            (False, lambda obj: str(obj.pk)),
        ],
    )
    def test_get_object_for_api_version(self, switch_active, lookup_value):
        obj = Example.objects.create()
        lookup = lookup_value(obj)

        queryset = Example.objects.all()

        with override_switch(API_UUID, active=switch_active):
            fetched = get_object_or_list_by_uuid_or_int(
                queryset=queryset,
                pk_or_pk_list=lookup,
                uuid_field="example_id",
            )

        assert fetched.pk == obj.pk

    @override_switch(API_UUID, active=True)
    def test_get_object_for_api_version_404(self):
        obj = Example.objects.create()
        queryset = Example.objects.all()

        with pytest.raises(Http404):
            get_object_or_list_by_uuid_or_int(
                queryset=queryset,
                pk_or_pk_list=str(obj.pk),
                uuid_field="example_id",
            )


@pytest.mark.django_db
@pytest.mark.usefixtures("create_example_table")
class TestGetListForApiVersion:
    @pytest.mark.parametrize(
        "switch_active,lookup_list",
        [
            (True, lambda obj_list: [str(obj.example_id) for obj in obj_list]),
            (False, lambda obj_list: [str(obj.example_id) for obj in obj_list]),
            (False, lambda obj_list: [str(obj.pk) for obj in obj_list]),
        ],
    )
    def test_get_list_for_api_version(self, switch_active, lookup_list):
        obj1 = Example.objects.create()
        obj2 = Example.objects.create()
        obj_list = [obj1, obj2]

        queryset = Example.objects.all()

        with override_switch(API_UUID, active=switch_active):
            fetched = get_object_or_list_by_uuid_or_int(
                queryset=queryset,
                pk_or_pk_list=lookup_list(obj_list),
                uuid_field="example_id",
            )
        assert {obj.pk for obj in fetched} == {obj1.pk, obj2.pk}


@pytest.mark.django_db
@pytest.mark.usefixtures("create_example_table")
class TestGetObjectByUuidOrInt:
    def test_int_lookup_returns_object(self):
        obj = Example.objects.create()
        fetched = get_object_by_uuid_or_int(Example.objects.all(), str(obj.pk), uuid_field="example_id")
        assert fetched.pk == obj.pk

    def test_uuid_lookup_returns_object(self):
        example_id = uuid.uuid4()
        obj = Example.objects.create(example_id=example_id)
        fetched = get_object_by_uuid_or_int(Example.objects.all(), str(example_id), uuid_field="example_id")
        assert fetched.pk == obj.pk

    @pytest.mark.parametrize("value", ["not-a-uuid-or-int", " 123", "0123", "123abc"])
    def test_invalid_value_raises_404(self, value):
        Example.objects.create()
        with pytest.raises(Http404):
            get_object_by_uuid_or_int(Example.objects.all(), value, uuid_field="example_id")

    def test_filtered_queryset(self):
        Example.objects.create()
        obj2 = Example.objects.create()
        qs = Example.objects.filter(pk=obj2.pk)
        fetched = get_object_by_uuid_or_int(qs, str(obj2.pk), uuid_field="example_id")
        assert fetched.pk == obj2.pk
