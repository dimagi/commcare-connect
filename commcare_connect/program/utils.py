from enum import IntEnum

from django.http import Http404

from commcare_connect.opportunity.models import Opportunity
from commcare_connect.utils.db import get_object_by_uuid_or_int
from commcare_connect.utils.permission_const import ALL_ORG_ACCESS


class AccessLevel(IntEnum):
    NONE = 0
    VIEW = 1
    STANDARD = 2
    MANAGE = 3

    @staticmethod
    def effective(org_level: "AccessLevel", user_level: "AccessLevel") -> "AccessLevel":
        return min(org_level, user_level)


def user_org_access(membership) -> AccessLevel:
    if not membership:
        return AccessLevel.NONE
    if membership.is_admin:
        return AccessLevel.MANAGE
    if membership.is_member:
        return AccessLevel.STANDARD
    if membership.is_viewer:
        return AccessLevel.VIEW
    return AccessLevel.NONE


def org_program_access(org, program) -> AccessLevel:
    if not org or not program:
        return AccessLevel.NONE
    if org.id in (program.organization_id, program.funder_id):
        return AccessLevel.MANAGE
    if program.watchers.filter(id=org.id).exists():
        return AccessLevel.VIEW
    return AccessLevel.NONE


def program_access_level_from_request(request, program) -> AccessLevel:
    base_access = _base_access_level(request)
    if base_access is not None:
        return base_access

    org_level = org_program_access(request.org, program)

    return AccessLevel.effective(org_level, user_org_access(request.org_membership))


def opportunity_access_level_from_request(request, opportunity) -> AccessLevel:
    if not opportunity:
        return AccessLevel.NONE
    base_access = _base_access_level(request)
    if base_access is not None:
        return base_access

    org_level = org_opportunity_access(request.org, opportunity)

    return AccessLevel.effective(org_level, user_org_access(request.org_membership))


def org_opportunity_access(org, opportunity) -> AccessLevel:
    """The one delivering it, supervising it and the one running its program. Watcher has view access."""
    if not org or not opportunity:
        return AccessLevel.NONE
    if org.id in (opportunity.organization_id, opportunity.supervising_organization_id):
        return AccessLevel.MANAGE
    return org_program_access(org, opportunity.program)


def opportunity_managing_org_ids(opportunity) -> set:
    """Every org with MANAGE access to this opportunity, independent of any request."""
    org_ids = {
        opportunity.organization_id,
        opportunity.supervising_organization_id,
        opportunity.program.organization_id,
    }
    if opportunity.program.funder_id:
        org_ids.add(opportunity.program.funder_id)
    return org_ids


def opportunity_by_id(opp_id) -> Opportunity | None:
    queryset = Opportunity.objects.select_related("program", "organization")
    try:
        return get_object_by_uuid_or_int(queryset, str(opp_id), uuid_field="opportunity_id")
    except Http404:
        return None


def org_access_level_from_request(request) -> AccessLevel:
    """Access level for org-related operations depends on the user's role.
    Only the organization's users can access these operations.
    """
    base_access = _base_access_level(request)
    if base_access is not None:
        return base_access
    return user_org_access(request.org_membership)


def _base_access_level(request) -> AccessLevel | None:
    if not request.org:
        return AccessLevel.NONE
    if request.user.has_perm(ALL_ORG_ACCESS):
        return AccessLevel.MANAGE
    return None


def is_org_pm(request):
    return request.org.program_manager and (
        (request.org_membership != None and request.org_membership.is_admin) or request.user.is_superuser  # noqa: E711
    )


def is_opportunity_nm(request, opportunity) -> bool:
    """The network manager is the org delivering the opportunity."""
    return _can_manage_opportunity(request, opportunity) and request.org.id == opportunity.organization_id


def is_opportunity_pm(request, opportunity) -> bool:
    """Anyone else who can manage the opportunity reaches it from the program side."""
    return _can_manage_opportunity(request, opportunity) and request.org.id != opportunity.organization_id


def _can_manage_opportunity(request, opportunity) -> bool:
    return opportunity_access_level_from_request(request, opportunity) is AccessLevel.MANAGE


def populate_currency_and_country_fk_for_model(apps, model_name, app_label, total_label):
    """
    Migration util to populate currency_fk and country fields for a opportunity/program
    """
    Model = apps.get_model(app_label, model_name)
    Currency = apps.get_model("opportunity", "Currency")
    Country = apps.get_model("opportunity", "Country")

    # Build lookup dictionaries
    code_to_currency = {cur.code: cur for cur in Currency.objects.all()}
    currency_to_countries = {}
    for country in Country.objects.all():
        if country.currency_id:
            currency_to_countries.setdefault(country.currency_id, []).append(country)

    BATCH_SIZE = 100
    qs = Model.objects.exclude(currency__isnull=True).exclude(currency="").only("id", "currency").order_by("id")
    total = qs.count()
    print(f"Populating {total_label} currency_fk & country for {total} records...")

    for start in range(0, total, BATCH_SIZE):
        batch = list(qs[start : start + BATCH_SIZE])  # noqa: E203
        for record in batch:
            raw_code = (record.currency or "").strip().upper()
            if not raw_code:
                record.currency_fk = None
                record.country = None
                continue

            if raw_code not in code_to_currency:
                # Set to USD when code is incorrect
                currency_obj = code_to_currency["USD"]
                code_to_currency[currency_obj.code] = currency_obj
            else:
                currency_obj = code_to_currency[raw_code]

            record.currency_fk = currency_obj
            countries = currency_to_countries.get(currency_obj.code, [])
            record.country = countries[0] if len(countries) == 1 else None

        Model.objects.bulk_update(batch, ["currency_fk", "country"], batch_size=BATCH_SIZE)

    print(f"Finished populating {total_label} currency_fk and country fields.")
