import pytest
from django.core.management import call_command

from commcare_connect.commcarehq.tests.fake_hq import serve_fake_hq
from commcare_connect.microplanning.const import SERVICE_DELIVERY_UNIT_SLUG
from commcare_connect.microplanning.management.commands.generate_microplanning_data import DEMO_OPP_NAME, Command
from commcare_connect.microplanning.models import (
    InaccessibilityRequestStatus,
    WorkArea,
    WorkAreaInaccessibilityRequest,
    WorkAreaStatus,
)
from commcare_connect.microplanning.services import bulk_create_or_update_cases_by_work_areas
from commcare_connect.opportunity.models import CompletedWork, Opportunity, UserVisit, VisitReviewStatus
from commcare_connect.opportunity.tests.factories import DeliverUnitFactory, PaymentUnitFactory


@pytest.mark.django_db
def test_reuses_existing_pairing(opportunity):
    """Deliver unit already owned by one of this opportunity's payment units keeps that pairing."""
    existing_pu = PaymentUnitFactory(opportunity=opportunity, name="Their Own Unit")
    du = DeliverUnitFactory(app=opportunity.deliver_app, slug=SERVICE_DELIVERY_UNIT_SLUG, payment_unit=existing_pu)

    units = Command().ensure_payment_units(opportunity)

    pu, paired_du = units[0]
    assert paired_du.pk == du.pk
    assert pu.pk == existing_pu.pk
    du.refresh_from_db()
    assert du.payment_unit_id == existing_pu.pk


@pytest.mark.django_db
def test_repoints_foreign_pairing(opportunity):
    """Deliver unit on another opportunity's payment unit gets repointed at the seeded one."""
    foreign_pu = PaymentUnitFactory(name="Elsewhere")
    assert foreign_pu.opportunity_id != opportunity.id
    du = DeliverUnitFactory(app=opportunity.deliver_app, slug=SERVICE_DELIVERY_UNIT_SLUG, payment_unit=foreign_pu)

    pu, paired_du = Command().ensure_payment_units(opportunity)[0]

    assert paired_du.pk == du.pk
    assert pu.opportunity_id == opportunity.id
    du.refresh_from_db()
    assert du.payment_unit_id == pu.pk


@pytest.mark.django_db
def test_seeds_from_scratch(opportunity):
    units = Command().ensure_payment_units(opportunity)
    assert len(units) == 2
    for pu, du in units:
        assert du.payment_unit_id == pu.pk
        assert pu.opportunity_id == opportunity.id


@pytest.mark.django_db
def test_idempotent(opportunity):
    first = Command().ensure_payment_units(opportunity)
    second = Command().ensure_payment_units(opportunity)
    assert [(p.pk, d.pk) for p, d in first] == [(p.pk, d.pk) for p, d in second]


@pytest.mark.django_db
def test_seeded_visits_accrue_payment():
    """Approved visits must also agree, or CompletedWork.approved_count counts none of them."""
    call_command(
        "generate_microplanning_data", clusters=1, grid=2, groups_per_cluster=1, workers=1, no_admin=True, force=True
    )

    visits = UserVisit.objects.filter(form_json={"seeded": True})
    assert visits.exists()
    assert not visits.exclude(review_status=VisitReviewStatus.agree).exists()

    completed_works = CompletedWork.objects.filter(uservisit__in=visits).distinct()
    assert completed_works
    assert all(cw.approved_count > 0 and cw.payment_accrued > 0 for cw in completed_works)


@pytest.mark.django_db
def test_cell_sizes_cycle_over_clusters():
    """Several --cell-size values put areas at different scales in one opportunity, which is what
    makes the map's auto-zoom cap observable."""
    call_command(
        "generate_microplanning_data",
        clusters=3,
        grid=2,
        groups_per_cluster=1,
        workers=1,
        no_admin=True,
        force=True,
        cell_size=[0.04, 0.008],
    )

    sizes = {}
    for prefix in ("north", "central", "south"):
        area = WorkArea.objects.filter(slug__startswith=f"{prefix}-").first()
        x1, _, x2, _ = area.boundary.extent
        sizes[prefix] = pytest.approx(x2 - x1, abs=1e-9)

    assert sizes == {"north": 0.04, "central": 0.008, "south": 0.04}


@pytest.mark.django_db
def test_every_requested_inaccessible_area_has_a_pending_request():
    call_command(
        "generate_microplanning_data",
        clusters=1,
        grid=4,
        groups_per_cluster=2,
        workers=1,
        inaccessible_requests=3,
        no_admin=True,
        force=True,
    )

    requested = WorkArea.objects.filter(status=WorkAreaStatus.REQUEST_FOR_INACCESSIBLE)
    pending = WorkAreaInaccessibilityRequest.objects.filter(status=InaccessibilityRequestStatus.PENDING)

    assert requested.count() == 3
    assert sorted(pending.values_list("work_area_id", flat=True)) == sorted(requested.values_list("id", flat=True))
    # Distinct visit dates, so the sidebar's oldest-first ordering has something to order by.
    assert len(set(pending.values_list("date_of_visit", flat=True))) == 3
    # Unassigned areas are out of scope for every microplanning metric, so a seeded request must
    # never sit on one.
    assert not requested.filter(opportunity_access__isnull=True).exists()


@pytest.mark.django_db
def test_fake_hq_option_lets_assigned_work_areas_sync_to_the_fake_hq():
    """Assignment needs the opportunity's API key on the fake HQ and an HQ user for each worker."""
    with serve_fake_hq() as hq:
        call_command(
            "generate_microplanning_data",
            clusters=1,
            grid=2,
            groups_per_cluster=1,
            workers=2,
            no_admin=True,
            force=True,
            fake_hq=hq.url,
        )
        opportunity = Opportunity.objects.get(name=DEMO_OPP_NAME)
        assigned = list(WorkArea.objects.filter(opportunity=opportunity, opportunity_access__isnull=False))

        cases = bulk_create_or_update_cases_by_work_areas(work_areas=assigned, opportunity=opportunity)

    assert assigned
    assert len(cases) == len(assigned)


@pytest.mark.django_db
def test_fake_hq_option_defaults_to_the_fake_hq_port_and_can_be_rerun():
    for _ in range(2):
        call_command(
            "generate_microplanning_data",
            "--fake-hq",
            clusters=1,
            grid=2,
            groups_per_cluster=1,
            workers=1,
            no_admin=True,
            force=True,
        )

    opportunity = Opportunity.objects.get(name=DEMO_OPP_NAME)
    assert opportunity.api_key.hq_server.url == "http://localhost:8001"
