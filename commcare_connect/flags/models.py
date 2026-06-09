from django.db import models
from django.db.models.signals import m2m_changed
from django.utils.translation import gettext_lazy
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
        verbose_name=gettext_lazy("Workspaces"),
        help_text=gettext_lazy("Activate this flag for these workspaces."),
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
        filters = (
            models.Q(users=user)
            | models.Q(organizations__members=user)
            | models.Q(opportunities__organization__members=user)
            | models.Q(opportunities__opportunityaccess__user=user)
            | models.Q(programs__organization__members=user)
        )

        if include_role_flags:
            filters |= models.Q(everyone=True)
            if user.is_staff:
                filters |= models.Q(staff=True)
            if user.is_superuser:
                filters |= models.Q(superusers=True)

        return cls.objects.filter(filters).distinct()

    @classmethod
    def is_flag_active_for_request(cls, request, flag_name: str):
        user = getattr(request, "user", None)
        if not (user and user.is_authenticated):
            return False
        flag = cls.get(flag_name)
        if flag.pk is None:
            return False

        if flag.everyone:
            return True
        if flag.staff and user.is_staff:
            return True
        if flag.superusers and user.is_superuser:
            return True
        if user.pk in flag._get_user_ids():
            return True

        opportunity = getattr(request, "opportunity", None)
        if opportunity and flag.is_active_for(opportunity):
            return True
        program = _get_program_for_opportunity(opportunity)
        if program and flag.is_active_for(program):
            return True
        org = getattr(request, "org", None)
        if org and flag.is_active_for(org):
            return True

        return False

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


def _get_program_for_opportunity(opportunity):
    if not (opportunity and opportunity.managed):
        return None
    managed_opp = getattr(opportunity, "managedopportunity", None)
    return managed_opp.program if managed_opp else None


def _flush_flag_relation_cache(sender, instance, action, **kwargs):
    from waffle.signals import flag_membership_changed

    flag_membership_changed(sender, instance, action, **kwargs)


# Ties the custom flag relationships to the m2m_changed signal
# to ensure flag's cache is cleared after editing
for _relation in ("organizations", "opportunities", "programs"):
    m2m_changed.connect(
        _flush_flag_relation_cache,
        sender=getattr(Flag, _relation).through,
        dispatch_uid=f"commcare_connect.flag.{_relation}.through",
    )
