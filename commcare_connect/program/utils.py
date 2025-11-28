from commcare_connect.cache import quickcache
from commcare_connect.program.models import ManagedOpportunity


@quickcache(vary_on=["opp_id"], timeout=60 * 60 * 24)
def get_managed_opp(opp_id) -> ManagedOpportunity | None:
    return ManagedOpportunity.objects.select_related("program__organization").filter(id=opp_id).first()


def is_program_manager(request):
    return request.org.program_manager and (
        (request.org_membership != None and request.org_membership.is_admin) or request.user.is_superuser  # noqa: E711
    )


def is_program_manager_of_opportunity(request, opp_id) -> bool:
    managed_opp = get_managed_opp(opp_id)
    return bool(
        managed_opp
        and managed_opp.managed
        and managed_opp.program.organization.slug == request.org.slug
        and is_program_manager(request)
    )


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
                currency_obj, _ = Currency.objects.get_or_create(
                    code=raw_code,
                    defaults={"name": "Unknown Name", "is_valid": False},
                )
                code_to_currency[currency_obj.code] = currency_obj
            else:
                currency_obj = code_to_currency[raw_code]

            record.currency_fk = currency_obj
            countries = currency_to_countries.get(currency_obj.code, [])
            record.country = countries[0] if len(countries) == 1 else None

        Model.objects.bulk_update(batch, ["currency_fk", "country"], batch_size=BATCH_SIZE)

    print(f"Finished populating {total_label} currency_fk and country fields.")
