from enum import IntEnum

from commcare_connect.cache import quickcache
from commcare_connect.opportunity.models import Opportunity
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


def _base_access_level(request) -> AccessLevel | None:
    if not request.org:
        return AccessLevel.NONE
    if request.user.has_perm(ALL_ORG_ACCESS):
        return AccessLevel.MANAGE
    return None


@quickcache(vary_on=["opp_id"], timeout=60 * 60 * 24)
def get_managed_opp(opp_id) -> Opportunity | None:
    queryset = Opportunity.objects.select_related("program__organization")
    if str(opp_id).isdigit():
        return queryset.filter(pk=int(opp_id)).first()
    return queryset.filter(opportunity_id=opp_id).first()


def is_org_pm(request):
    return request.org.program_manager and (
        (request.org_membership != None and request.org_membership.is_admin) or request.user.is_superuser  # noqa: E711
    )


def is_opportunity_pm(request, opp_id) -> bool:
    managed_opp = get_managed_opp(opp_id)
    return bool(managed_opp and managed_opp.program.organization.slug == request.org.slug and is_org_pm(request))


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
