import uuid
from enum import IntEnum

from commcare_connect.cache import quickcache
from commcare_connect.opportunity.models import Opportunity
from commcare_connect.program.models import Program
from commcare_connect.utils.permission_const import ALL_ORG_ACCESS


class AccessLevel(IntEnum):
    NONE = 0
    VIEW = 1
    MANAGE = 2

    @staticmethod
    def effective(relationship: "AccessLevel", organization_role: "AccessLevel") -> "AccessLevel":
        return min(relationship, organization_role)


def org_program_role(org, program) -> AccessLevel:
    if not org or not program:
        return AccessLevel.NONE
    if org.id in {program.organization_id, program.funder_id}:
        return AccessLevel.MANAGE
    if program.watchers.filter(id=org.id).exists():
        return AccessLevel.VIEW
    return AccessLevel.NONE


def organization_role_level(membership) -> AccessLevel:
    if not membership:
        return AccessLevel.NONE
    return AccessLevel.MANAGE if membership.is_admin else AccessLevel.VIEW


def request_can_manage_program(request) -> bool:
    return request_access_level(request) is AccessLevel.MANAGE


def request_can_view_program(request) -> bool:
    return request_access_level(request) in (AccessLevel.VIEW, AccessLevel.MANAGE)


def request_access_level(request) -> AccessLevel:
    if not request.org:
        return AccessLevel.NONE
    if request.user.has_perm(ALL_ORG_ACCESS):
        return AccessLevel.MANAGE

    program = program_from_request(request)
    if program is None:
        # Only program:init(create) and program:home(NM vs PM switch) reach here; neither has a program yet.
        # Uses exsiting permission to determine access level.
        relationship = AccessLevel.MANAGE if request.org.program_manager else AccessLevel.NONE
    else:
        relationship = org_program_role(request.org, program)

    return AccessLevel.effective(
        relationship=relationship,
        organization_role=organization_role_level(request.org_membership),
    )


def program_from_request(request) -> Program | None:
    if not hasattr(request, "_cached_program"):
        request._cached_program = _resolve_program(request)
    return request._cached_program


def opportunity_by_id(opp_id) -> Opportunity | None:
    """Look an opportunity up by integer pk or opportunity_id UUID.

    Returns None rather than raising for a malformed id: `<slug:opp_id>` matches
    non-UUID strings, and handing one to a UUIDField filter raises ValidationError.
    """
    if str(opp_id).isdigit():
        lookup = {"pk": int(opp_id)}
    else:
        try:
            lookup = {"opportunity_id": uuid.UUID(str(opp_id))}
        except ValueError:
            return None
    return Opportunity.objects.filter(**lookup).select_related("program").first()


def _resolve_program(request) -> Program | None:
    """
    Resolve the program from the most specific request context.

    Prefer the opportunity's program (all opportunity urls) and
    fall back to the program `pk` (all program related urls).
    """
    opportunity = getattr(request, "opportunity", None)
    if opportunity is not None:
        return opportunity.program

    kwargs = getattr(getattr(request, "resolver_match", None), "kwargs", None) or {}

    opp_id = kwargs.get("opp_id")
    if opp_id:
        opportunity = opportunity_by_id(opp_id)
        if opportunity is not None:
            return opportunity.program

    program_id = _program_id_from_kwargs(request, kwargs)
    if program_id:
        return Program.objects.filter(program_id=program_id).first()

    return None


def _program_id_from_kwargs(request, kwargs) -> str | None:
    """The program UUID behind a `pk` kwarg -- on program URLs only."""
    app_names = getattr(getattr(request, "resolver_match", None), "app_names", None) or []
    if "program" not in app_names:
        return None

    program_id = kwargs.get("pk")
    if not program_id:
        return None
    try:
        uuid.UUID(str(program_id))
    except ValueError:
        return None
    return program_id


@quickcache(vary_on=["opp_id"], timeout=60 * 60 * 24)
def get_managed_opp(opp_id) -> Opportunity | None:
    queryset = Opportunity.objects.select_related("program__organization")
    if str(opp_id).isdigit():
        return queryset.filter(pk=int(opp_id)).first()
    return queryset.filter(opportunity_id=opp_id).first()


def is_org_pm(request) -> bool:
    return request_can_manage_program(request)


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
