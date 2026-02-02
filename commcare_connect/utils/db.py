import uuid

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import QuerySet
from django.http import Http404
from django.shortcuts import get_list_or_404, get_object_or_404
from django.utils.text import slugify


class BaseModel(models.Model):
    created_by = models.CharField(max_length=255)
    modified_by = models.CharField(max_length=255)
    date_created = models.DateTimeField(auto_now_add=True)
    date_modified = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


def slugify_uniquely(value, model, slugfield="slug"):
    """Returns a slug on a name which is unique within a model's table
    Taken from https://code.djangoproject.com/wiki/SlugifyUniquely
    """
    suffix = 0
    potential = base = slugify(value)
    while True:
        if suffix:
            potential = "-".join([base, str(suffix)])

        if not model.objects.filter(**{slugfield: potential}).count():
            return potential
        # we hit a conflicting slug, so bump the suffix & try again
        suffix += 1


def get_object_or_list_for_api_version(request, queryset, pk_or_pk_list, uuid_field, int_field="pk"):
    """
    Fetch object correctly based on what version request is using.

    V2 uses the UUID model field exclusively, while V1 supports both int IDs and UUIDs.
    Fetch object from queryset using appropriate field based on request API version.
    """
    is_pk_list = isinstance(pk_or_pk_list, (list, tuple))
    if request.version == "1.0":
        func = get_list_by_uuid_or_int if is_pk_list else get_object_by_uuid_or_int
        return func(
            queryset,
            pk_or_pk_list,
            uuid_field=uuid_field,
            int_field=int_field,
        )
    else:
        try:
            if is_pk_list:
                uuid_list = [uuid.UUID(val) for val in pk_or_pk_list]
                return get_list_or_404(queryset, **{f"{uuid_field}__in": uuid_list})
            return get_object_or_404(queryset, **{uuid_field: pk_or_pk_list})
        except (ValidationError, ValueError):
            raise Http404("Invalid UUID format.")


def get_object_by_uuid_or_int(
    queryset: QuerySet,
    lookup_value: str,
    uuid_field: str,
    int_field: str = "pk",
):
    if lookup_value.isdigit():
        return get_object_or_404(queryset, **{int_field: int(lookup_value)})

    try:
        uuid_val = uuid.UUID(lookup_value)
        return get_object_or_404(queryset, **{uuid_field: uuid_val})
    except ValueError:
        raise Http404(f"No {queryset.model._meta.object_name} matches the given query.")


def get_list_by_uuid_or_int(queryset: QuerySet, lookup_list: list[str], uuid_field: str, int_field: str = "pk"):
    if all(val.isdigit() for val in lookup_list):
        lookup_list_int = [int(val) for val in lookup_list]
        return get_list_or_404(queryset, **{f"{int_field}__in": lookup_list_int})

    try:
        lookup_list_uuid = [uuid.UUID(val) for val in lookup_list]
        return get_list_or_404(queryset, **{f"{uuid_field}__in": lookup_list_uuid})
    except ValueError:
        raise Http404(f"No {queryset.model._meta.object_name} matches the given query.")
