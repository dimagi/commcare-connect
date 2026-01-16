from django.db import models
from waffle.models import CACHE_EMPTY, AbstractUserFlag
from waffle.utils import get_cache, get_setting, keyfmt

from commcare_connect.opportunity.models import Opportunity
from commcare_connect.organization.models import Organization
from commcare_connect.program.models import Program


# See https://waffle.readthedocs.io/en/stable/types/flag.html#custom-flag-models
class Flag(AbstractUserFlag):
    # Mapping of relation names to their cache key settings (key name, default value)
    RELATION_CACHE_KEYS = {
        "organizations": ("FLAG_ORGANIZATIONS_CACHE_KEY", "flag:%s:organizations"),
        "opportunities": ("FLAG_OPPORTUNITIES_CACHE_KEY", "flag:%s:opportunities"),
        "programs": ("FLAG_PROGRAMS_CACHE_KEY", "flag:%s:programs"),
    }

    organizations = models.ManyToManyField(
        Organization,
        blank=True,
        help_text="Activate this flag for these organizations.",
    )
    opportunities = models.ManyToManyField(
        Opportunity,
        blank=True,
        help_text="Activate this flag for these opportunities.",
    )
    programs = models.ManyToManyField(
        Program,
        blank=True,
        help_text="Activate this flag for these programs.",
    )

    def get_flush_keys(self, flush_keys=None):
        flush_keys = super().get_flush_keys(flush_keys)
        for key_name, default_value in self.RELATION_CACHE_KEYS.values():
            cache_key = get_setting(key_name, default_value)
            flush_keys.append(keyfmt(cache_key, self.name))

        return flush_keys

    @classmethod
    def active_flags_for_user(cls, user, include_role_flags=False):
        if not include_role_flags:
            return cls.objects.filter(users__in=[user])

        conditions = models.Q(users__in=[user]) | models.Q(everyone=True)
        if user.is_staff:
            conditions |= models.Q(staff=True)
        if user.is_superuser:
            conditions |= models.Q(superusers=True)

        return cls.objects.filter(conditions)

    def is_active_for(self, obj: Organization | Opportunity | Program):
        if isinstance(obj, Organization):
            organization_ids = self._get_ids_for_relation("organizations")
            return obj.pk in organization_ids
        elif isinstance(obj, Opportunity):
            opportunity_ids = self._get_ids_for_relation("opportunities")
            return obj.pk in opportunity_ids
        elif isinstance(obj, Program):
            program_ids = self._get_ids_for_relation("programs")
            return obj.pk in program_ids

        return False

    def _get_ids_for_relation(self, relation_name):
        """Get cached IDs for a relation using its configured cache keys."""
        key_name, default_value = self.RELATION_CACHE_KEYS[relation_name]
        cache_key = get_setting(key_name, default_value)
        return self._get_relation_ids(relation_name, cache_key)

    def _get_relation_ids(self, relation_name, cache_key_template):
        """Generic method to get cached IDs for a ManyToMany relationship."""
        cache = get_cache()
        cache_key = keyfmt(cache_key_template, self.name)
        cached = cache.get(cache_key)
        if cached == CACHE_EMPTY:
            return set()
        if cached:
            return cached

        relation = getattr(self, relation_name)
        ids = set(relation.all().values_list("pk", flat=True))
        if not ids:
            cache.add(cache_key, CACHE_EMPTY)
            return set()

        cache.add(cache_key, ids)
        return ids
