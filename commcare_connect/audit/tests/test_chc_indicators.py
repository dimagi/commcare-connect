from __future__ import annotations

import datetime
import random
from datetime import timezone

import pytest

from commcare_connect.audit.chc_indicators import (
    FEMALE,
    SERVICE_DELIVERY_SLUG,
    YES,
    AgeHeaping,
    CampingRatio,
    GenderRatioDeviation,
    InaccessibleWARateEarlyWarning,
    InaccessibleWARateLastCompletedWAG,
    MUACDistributionPatternIndex,
    MUACPhotoCompliance,
    VaccineCardPhotoCompliance,
    VaccineRate,
    WACoverageToVisitRatio,
)
from commcare_connect.microplanning.models import WorkArea, WorkAreaStatus
from commcare_connect.microplanning.tests.factories import WorkAreaFactory, WorkAreaGroupFactory
from commcare_connect.opportunity.tests.factories import DeliverUnitFactory, OpportunityAccessFactory, UserVisitFactory

PERIOD_START = datetime.date(2026, 4, 13)  # Monday
PERIOD_END = datetime.date(2026, 4, 19)  # Sunday
IN_PERIOD = datetime.datetime(2026, 4, 15, 12, 0, tzinfo=timezone.utc)
OUT_OF_PERIOD = datetime.datetime(2026, 4, 5, 12, 0, tzinfo=timezone.utc)
AFTER_PERIOD = datetime.datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)


def make_visit(access, work_area=None, visit_date=IN_PERIOD, **kwargs):
    kwargs.setdefault("deliver_unit", DeliverUnitFactory(slug=SERVICE_DELIVERY_SLUG))
    return UserVisitFactory(
        opportunity=access.opportunity,
        user=access.user,
        opportunity_access=access,
        work_area=work_area,
        visit_date=visit_date,
        completed_work=None,
        **kwargs,
    )


def make_form_visit(access, form_payload, **kwargs):
    return make_visit(access, form_json={"form": form_payload}, **kwargs)


def make_visits(n, access, **kwargs):
    for _ in range(n):
        make_visit(access, **kwargs)


def make_was(wag, access, *, inaccessible=0, reached=0, visited=0, not_visited=0) -> WorkArea | None:
    """Create WAs with the given status counts; return the first non-NOT_VISITED WA created."""
    first_terminal = None
    for status, count in [
        (WorkAreaStatus.INACCESSIBLE, inaccessible),
        (WorkAreaStatus.EXPECTED_VISIT_REACHED, reached),
        (WorkAreaStatus.VISITED, visited),
        (WorkAreaStatus.NOT_VISITED, not_visited),
    ]:
        for _ in range(count):
            wa = WorkAreaFactory(
                opportunity=wag.opportunity, work_area_group=wag, opportunity_access=access, status=status
            )
            if first_terminal is None and status != WorkAreaStatus.NOT_VISITED:
                first_terminal = wa
    return first_terminal


def visit_all_was(access, wag, **kwargs):
    for wa in WorkArea.objects.filter(work_area_group=wag):
        make_visit(access, work_area=wa, **kwargs)


def make_closed_wag(access, *, inaccessible=0, reached=6, visit_date=IN_PERIOD):
    """Create a fully-closed WAG (all terminal WAs with visits) and return it."""
    wag = WorkAreaGroupFactory(opportunity=access.opportunity)
    make_was(wag, access, inaccessible=inaccessible, reached=reached)
    visit_all_was(access, wag, visit_date=visit_date)
    return wag


def make_access_and_wag():
    access = OpportunityAccessFactory()
    return access, WorkAreaGroupFactory(opportunity=access.opportunity)


# ── visit builder helpers ──────────────────────────────────────────────────────


def _visit_with_gender(access, gender, **kwargs):
    return make_form_visit(access, {"additional_case_info": {"childs_gender": gender}}, **kwargs)


def _visit_with_age(access, age, **kwargs):
    # Real forms store age in months as a string under the plural key.
    additional = {"childs_age_in_months": str(age)}
    return make_form_visit(access, {"additional_case_info": additional}, **kwargs)


def _visit_with_muac_photo(access, has_photo=True, age_months=12, **kwargs):
    muac_group = {"muac_consent_group": {"muac_consent": "yes"}}
    if has_photo:
        muac_group["muac_photo_link"] = "https://example.com/photo.jpg"
    return make_form_visit(
        access,
        {"additional_case_info": {"childs_age_in_months": str(age_months)}, "muac_group": muac_group},
        **kwargs,
    )


def _visit_with_vaccine(access, given=True, **kwargs):
    return make_form_visit(
        access,
        {"child_vaccine_group": {"vaccines_ql": {"received_any_vaccine": YES if given else "no"}}},
        **kwargs,
    )


def _visit_with_vaccine_card(access, has_photo=True, **kwargs):
    child_vaccine_group = {"vaccines_ql": {"received_any_vaccine": YES}}
    if has_photo:
        child_vaccine_group["vaccine_photo_folder"] = {"photo_link_vaccine": "https://example.com/card.jpg"}
    return make_form_visit(access, {"child_vaccine_group": child_vaccine_group}, **kwargs)


def _visit_with_muac(access, muac_cm, **kwargs):
    # Real forms store the MUAC measurement as a string under muac_group.
    return make_form_visit(
        access,
        {"muac_group": {"muac_display_group_2": {"muac_colour_display": {"soliciter_muac_cm": str(muac_cm)}}}},
        **kwargs,
    )


# Bell-shaped distribution centred around 13.5 cm — passes all 6 MUAC features
_rng = random.Random(42)
REALISTIC_MUAC: list[float] = [max(9.5, min(21.4, round(_rng.gauss(13.5, 1.5), 1))) for _ in range(100)]
del _rng


def make_muac_visits(access, measurements=REALISTIC_MUAC, **kwargs):
    for m in measurements:
        _visit_with_muac(access, m, **kwargs)


# ── base test class ────────────────────────────────────────────────────────────


class BaseIndicatorTest:
    calc = None

    def compute(self, access):
        return self.calc.compute(access, PERIOD_START, PERIOD_END)

    def run(self, access):
        return self.calc.run(access, PERIOD_START, PERIOD_END)

    def assert_insufficient_data(self, access):
        assert self.compute(access).sample_size == 0

    def assert_compute_result(self, access, *, value=None, sample=None, in_range=None):
        m = self.compute(access)
        if sample is not None:
            assert m.sample_size == sample
        if value is not None:
            assert m.value == pytest.approx(value)
        if in_range is not None:
            assert self.calc._in_range(m.value) is in_range
        return m

    def assert_insufficient_run(self, access):
        assert self.run(access).has_sufficient_data is False


# ── fresh access ⟹ insufficient data for every indicator ──────────────────────


@pytest.mark.django_db
@pytest.mark.parametrize(
    "calc",
    [
        CampingRatio(),
        GenderRatioDeviation(),
        MUACPhotoCompliance(),
        AgeHeaping(),
        WACoverageToVisitRatio(),
        InaccessibleWARateEarlyWarning(),
        InaccessibleWARateLastCompletedWAG(),
        VaccineRate(),
        VaccineCardPhotoCompliance(),
        MUACDistributionPatternIndex(),
    ],
    ids=[
        "CampingRatio",
        "GenderRatioDeviation",
        "MUACPhotoCompliance",
        "AgeHeaping",
        "WACoverageToVisitRatio",
        "InaccessibleWARateEarlyWarning",
        "InaccessibleWARateLastCompletedWAG",
        "VaccineRate",
        "VaccineCardPhotoCompliance",
        "MUACDistributionPatternIndex",
    ],
)
def test_fresh_access_returns_insufficient_data(calc):
    access = OpportunityAccessFactory()
    sample = calc.compute(access, PERIOD_START, PERIOD_END).sample_size
    assert sample == 0


@pytest.mark.django_db
class TestCampingRatio(BaseIndicatorTest):
    calc = CampingRatio()

    @pytest.mark.parametrize(
        "n_visits, n_buildings, expected_camping_count",
        [
            (1, 10, 0),  # 1/10 = 0.1, below threshold
            (13, 1, 1),  # 13/1 = 13 > 12, flagged
            (12, 1, 0),  # 12/1 = 12, not > 12, boundary is exclusive
        ],
        ids=["below_threshold", "above_threshold", "at_boundary_exclusive"],
    )
    def test_camping_threshold(self, n_visits, n_buildings, expected_camping_count):
        access = OpportunityAccessFactory()
        wa = WorkAreaFactory(opportunity=access.opportunity, building_count=n_buildings)
        make_visits(n_visits, access, work_area=wa)
        self.assert_compute_result(access, sample=1, value=expected_camping_count)

    def test_wa_with_zero_building_count_excluded(self):
        access = OpportunityAccessFactory()
        wa = WorkAreaFactory(opportunity=access.opportunity, building_count=0)
        make_visit(access, work_area=wa)
        self.assert_insufficient_data(access)

    def test_visits_outside_period_not_counted(self):
        access = OpportunityAccessFactory()
        wa = WorkAreaFactory(opportunity=access.opportunity, building_count=1)
        make_visits(13, access, work_area=wa, visit_date=OUT_OF_PERIOD)
        self.assert_insufficient_data(access)

    def test_only_camping_wa_flagged(self):
        access = OpportunityAccessFactory()
        wa_camping = WorkAreaFactory(opportunity=access.opportunity, building_count=1)
        wa_ok = WorkAreaFactory(opportunity=access.opportunity, building_count=100)
        make_visits(13, access, work_area=wa_camping)
        make_visit(access, work_area=wa_ok)
        self.assert_compute_result(access, sample=2, value=1)

    def test_run_marks_out_of_range_when_camping(self):
        access = OpportunityAccessFactory()
        wa = WorkAreaFactory(opportunity=access.opportunity, building_count=1)
        make_visits(13, access, work_area=wa)
        result = self.run(access)
        assert result.has_sufficient_data is True
        assert result.in_range is False


@pytest.mark.django_db
class TestGenderRatioDeviation(BaseIndicatorTest):
    calc = GenderRatioDeviation()

    @pytest.mark.parametrize(
        "female, male, expected_percent, in_range",
        [
            (5, 5, 50.0, True),  # 50/50 — within 40–60
            (10, 0, 100.0, False),  # all female — above 60
        ],
        ids=["balanced", "all_female"],
    )
    def test_ratio_threshold(self, female, male, expected_percent, in_range):
        access = OpportunityAccessFactory()
        for _ in range(female):
            _visit_with_gender(access, FEMALE)
        for _ in range(male):
            _visit_with_gender(access, "male_child")
        self.assert_compute_result(access, sample=female + male, value=expected_percent, in_range=in_range)

    @pytest.mark.parametrize("visit_date", [AFTER_PERIOD, OUT_OF_PERIOD], ids=["after_period", "before_period"])
    def test_visits_outside_period_excluded(self, visit_date):
        access = OpportunityAccessFactory()
        _visit_with_gender(access, FEMALE, visit_date=visit_date)
        self.assert_insufficient_data(access)

    def test_denominator_is_all_visits_not_just_with_gender(self):
        access = OpportunityAccessFactory()
        make_visit(access)  # no gender field
        _visit_with_gender(access, FEMALE)
        self.assert_compute_result(access, sample=2, value=50.0)


@pytest.mark.django_db
class TestMUACPhotoCompliance(BaseIndicatorTest):
    calc = MUACPhotoCompliance()

    def test_child_under_6_months_not_in_denominator(self):
        access = OpportunityAccessFactory()
        _visit_with_muac_photo(access, age_months=3)  # age <= 6, excluded
        self.assert_insufficient_data(access)

    def test_no_consent_still_in_denominator(self):
        access = OpportunityAccessFactory()
        make_visit(access, form_json={"form": {"additional_case_info": {"childs_age_in_months": "12"}}})
        sample = self.compute(access).sample_size
        assert sample == 1  # consent is irrelevant; age ≥6 makes this visit eligible

    @pytest.mark.parametrize(
        "with_photo, without_photo, expected_value, in_range",
        [
            (5, 0, 100.0, True),  # 100% compliance
            (1, 4, 20.0, False),  # 20% compliance, below 72% threshold
        ],
        ids=["full_compliance", "below_threshold"],
    )
    def test_compliance_threshold(self, with_photo, without_photo, expected_value, in_range):
        access = OpportunityAccessFactory()
        for _ in range(with_photo):
            _visit_with_muac_photo(access, has_photo=True)
        for _ in range(without_photo):
            _visit_with_muac_photo(access, has_photo=False)
        self.assert_compute_result(access, sample=with_photo + without_photo, value=expected_value, in_range=in_range)

    def test_empty_photo_link_not_counted(self):
        access = OpportunityAccessFactory()
        make_visit(
            access,
            form_json={
                "form": {
                    "additional_case_info": {"childs_age_in_months": "12"},
                    "muac_group": {"muac_consent_group": {"muac_consent": "yes"}, "muac_photo_link": ""},
                }
            },
        )
        self.assert_compute_result(access, value=0.0)


@pytest.mark.django_db
class TestAgeHeaping(BaseIndicatorTest):
    calc = AgeHeaping()

    @pytest.mark.parametrize(
        "ages, expected_rate, in_range",
        [
            ([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 0.0, True),  # no round years
            ([12, 24, 36, 48, 1, 2, 3, 4, 5, 6], 40.0, False),  # 4/10 heaped, above 19%
        ],
        ids=["no_round_years", "high_heaping"],
    )
    def test_heaping_rate(self, ages, expected_rate, in_range):
        access = OpportunityAccessFactory()
        for age in ages:
            _visit_with_age(access, age)
        self.assert_compute_result(access, sample=len(ages), value=expected_rate, in_range=in_range)

    def test_visits_after_period_end_excluded(self):
        access = OpportunityAccessFactory()
        _visit_with_age(access, 12, visit_date=AFTER_PERIOD)
        self.assert_insufficient_data(access)

    def test_age_13_not_heaped(self):
        access = OpportunityAccessFactory()
        for _ in range(5):
            _visit_with_age(access, 13)
        self.assert_compute_result(access, value=0.0)


@pytest.mark.django_db
class TestWACoverageToVisitRatio(BaseIndicatorTest):
    calc = WACoverageToVisitRatio()

    def test_balanced_coverage_and_visits_in_range(self):
        access, wag = make_access_and_wag()
        wa = WorkAreaFactory(
            opportunity=wag.opportunity,
            work_area_group=wag,
            opportunity_access=access,
            status=WorkAreaStatus.VISITED,
            expected_visit_count=10,
        )
        WorkAreaFactory(
            opportunity=wag.opportunity,
            work_area_group=wag,
            opportunity_access=access,
            status=WorkAreaStatus.NOT_VISITED,
            expected_visit_count=10,
        )
        make_visits(10, access, work_area=wa)
        self.assert_compute_result(access, sample=2, value=1.0, in_range=True)

    def test_high_coverage_low_visits_out_of_range(self):
        access, wag = make_access_and_wag()
        wa = WorkAreaFactory(
            opportunity=wag.opportunity,
            work_area_group=wag,
            opportunity_access=access,
            status=WorkAreaStatus.VISITED,
            expected_visit_count=100,
        )
        WorkAreaFactory(
            opportunity=wag.opportunity,
            work_area_group=wag,
            opportunity_access=access,
            status=WorkAreaStatus.NOT_VISITED,
            expected_visit_count=100,
        )
        make_visit(access, work_area=wa)
        value = self.compute(access).value
        assert value > 1.4

    def test_low_coverage_high_visits_out_of_range(self):
        access, wag = make_access_and_wag()
        wa = WorkAreaFactory(
            opportunity=wag.opportunity,
            work_area_group=wag,
            opportunity_access=access,
            status=WorkAreaStatus.NOT_VISITED,
            expected_visit_count=1,
        )
        make_visits(10, access, work_area=wa)
        value = self.compute(access).value
        assert value < 0.6

    def test_excluded_wa_not_counted_in_eligible_but_inaccessible_is(self):
        """EXCLUDED WAs must not count in eligible denominator; INACCESSIBLE WAs are included."""
        access, wag = make_access_and_wag()
        wa = WorkAreaFactory(
            opportunity=wag.opportunity,
            work_area_group=wag,
            opportunity_access=access,
            status=WorkAreaStatus.VISITED,
            expected_visit_count=10,
        )
        WorkAreaFactory(
            opportunity=wag.opportunity,
            work_area_group=wag,
            opportunity_access=access,
            status=WorkAreaStatus.EXCLUDED,
            expected_visit_count=10,
        )
        WorkAreaFactory(
            opportunity=wag.opportunity,
            work_area_group=wag,
            opportunity_access=access,
            status=WorkAreaStatus.INACCESSIBLE,
            expected_visit_count=10,
        )
        make_visits(10, access, work_area=wa)
        # VISITED + INACCESSIBLE = 2 eligible; coverage=1/2, visit_ratio=10/20 → ratio=1.0
        self.assert_compute_result(access, sample=2, value=1.0)

    def test_no_actual_visits_returns_insufficient_data(self):
        access, wag = make_access_and_wag()
        WorkAreaFactory(
            opportunity=wag.opportunity,
            work_area_group=wag,
            opportunity_access=access,
            status=WorkAreaStatus.NOT_VISITED,
            expected_visit_count=10,
        )
        self.assert_insufficient_data(access)

    def test_no_expected_visits_returns_insufficient_data(self):
        access, wag = make_access_and_wag()
        WorkAreaFactory(
            opportunity=wag.opportunity,
            work_area_group=wag,
            opportunity_access=access,
            status=WorkAreaStatus.NOT_VISITED,
            expected_visit_count=0,
        )
        make_visit(access)
        self.assert_insufficient_data(access)

    def test_wa_visited_after_period_end_not_counted_as_visited(self):
        """A WA whose only visits fall after period_end must not be counted as visited,
        even if its current status is VISITED."""
        access, wag = make_access_and_wag()
        wa_in_period = WorkAreaFactory(
            opportunity=wag.opportunity,
            work_area_group=wag,
            opportunity_access=access,
            status=WorkAreaStatus.VISITED,
            expected_visit_count=10,
        )
        wa_after_period = WorkAreaFactory(
            opportunity=wag.opportunity,
            work_area_group=wag,
            opportunity_access=access,
            status=WorkAreaStatus.VISITED,
            expected_visit_count=10,
        )
        make_visits(10, access, work_area=wa_in_period, visit_date=IN_PERIOD)
        make_visits(10, access, work_area=wa_after_period, visit_date=AFTER_PERIOD)
        # Only wa_in_period counts as visited at period_end → (1/2) / (10/20) = 1.0
        self.assert_compute_result(access, sample=2, value=1.0, in_range=True)


@pytest.mark.django_db
class TestInaccessibleWARateEarlyWarning(BaseIndicatorTest):
    calc = InaccessibleWARateEarlyWarning()

    def test_fewer_than_5_terminal_was_returns_insufficient_data(self):
        access, wag = make_access_and_wag()
        first_wa = make_was(wag, access, inaccessible=2, not_visited=1)
        make_visit(access, work_area=first_wa)
        self.assert_compute_result(access, sample=2)
        self.assert_insufficient_run(access)

    @pytest.mark.parametrize(
        "inaccessible, reached, not_visited, expected_value, in_range",
        [
            (1, 5, 1, 14.29, True),  # 1/7 of all assigned WAs, below 25% threshold
            (4, 1, 1, 66.67, False),  # 4/6 of all assigned WAs, above 25% threshold
            (2, 4, 2, 25.0, True),  # exactly 25% (2/8) is in range — boundary is inclusive
        ],
        ids=["below_threshold", "above_threshold", "at_threshold"],
    )
    def test_inaccessible_rate_threshold(self, inaccessible, reached, not_visited, expected_value, in_range):
        access, wag = make_access_and_wag()
        first_wa = make_was(wag, access, inaccessible=inaccessible, reached=reached, not_visited=not_visited)
        make_visit(access, work_area=first_wa)  # visit needed so _find_active_wag can locate this WAG
        # sample_size is the gating count (terminal WAs); the rate divides by all assigned WAs.
        self.assert_compute_result(access, sample=inaccessible + reached, value=expected_value, in_range=in_range)

    def test_run_reports_numerator_over_all_assigned_was(self):
        access, wag = make_access_and_wag()
        first_wa = make_was(wag, access, inaccessible=2, reached=4, not_visited=2)
        make_visit(access, work_area=first_wa)
        result = self.run(access)
        # numerator = inaccessible WAs; denominator = all assigned WAs (not the terminal sample).
        assert result.numerator == 2
        assert result.denominator == 8
        assert result.value == pytest.approx(25.0)

    def test_fully_closed_wag_not_treated_as_active(self):
        access, wag = make_access_and_wag()
        make_was(wag, access, visited=6)
        self.assert_insufficient_data(access)

    def test_most_recently_visited_wag_selected_as_active(self):
        access, wag_early = make_access_and_wag()
        wa_early = WorkAreaFactory(
            opportunity=wag_early.opportunity,
            work_area_group=wag_early,
            opportunity_access=access,
            status=WorkAreaStatus.NOT_VISITED,
        )
        make_visit(access, work_area=wa_early, visit_date=OUT_OF_PERIOD)

        wag_active = WorkAreaGroupFactory(opportunity=access.opportunity)
        make_was(wag_active, access, reached=5)
        wa_inaccessible = WorkAreaFactory(
            opportunity=wag_active.opportunity,
            work_area_group=wag_active,
            opportunity_access=access,
            status=WorkAreaStatus.INACCESSIBLE,
        )
        WorkAreaFactory(
            opportunity=wag_active.opportunity,
            work_area_group=wag_active,
            opportunity_access=access,
            status=WorkAreaStatus.NOT_VISITED,
        )
        make_visit(access, work_area=wa_inaccessible, visit_date=IN_PERIOD)

        self.assert_compute_result(access, sample=6, value=14.29)


@pytest.mark.django_db
class TestInaccessibleWARateLastCompletedWAG(BaseIndicatorTest):
    calc = InaccessibleWARateLastCompletedWAG()

    def test_no_completed_wag_returns_insufficient_data(self):
        access, wag = make_access_and_wag()
        WorkAreaFactory(
            opportunity=wag.opportunity,
            work_area_group=wag,
            opportunity_access=access,
            status=WorkAreaStatus.NOT_VISITED,  # not terminal → not completed
        )
        self.assert_insufficient_data(access)

    def test_all_visited_wag_not_treated_as_closed(self):
        """VISITED means visits started but expected count not yet reached — not a closed WAG."""
        access, wag = make_access_and_wag()
        make_was(wag, access, visited=6)
        visit_all_was(access, wag)
        self.assert_insufficient_data(access)

    def test_active_wag_not_selected(self):
        """A WAG with any non-closed WA is not completed."""
        access, wag = make_access_and_wag()
        WorkAreaFactory(
            opportunity=wag.opportunity,
            work_area_group=wag,
            opportunity_access=access,
            status=WorkAreaStatus.EXPECTED_VISIT_REACHED,
        )
        WorkAreaFactory(
            opportunity=wag.opportunity,
            work_area_group=wag,
            opportunity_access=access,
            status=WorkAreaStatus.NOT_VISITED,
        )
        self.assert_insufficient_data(access)

    @pytest.mark.parametrize(
        "inaccessible, reached, expected_value, in_range",
        [
            (1, 6, 14.29, True),  # ~14%, below 15% threshold
            (4, 2, 66.67, False),  # ~67%, above 15% threshold
        ],
        ids=["below_threshold", "above_threshold"],
    )
    def test_inaccessible_rate_threshold(self, inaccessible, reached, expected_value, in_range):
        access = OpportunityAccessFactory()
        make_closed_wag(access, inaccessible=inaccessible, reached=reached)
        self.assert_compute_result(access, sample=inaccessible + reached, value=expected_value, in_range=in_range)

    def test_excluded_was_not_counted_in_denominator(self):
        access, wag = make_access_and_wag()
        make_was(wag, access, inaccessible=1, reached=6)
        WorkAreaFactory(
            opportunity=wag.opportunity,
            work_area_group=wag,
            opportunity_access=access,
            status=WorkAreaStatus.EXCLUDED,
        )
        visit_all_was(access, wag)
        self.assert_compute_result(access, sample=7, value=14.29)  # EXCLUDED WA not counted in sample

    def test_most_recently_completed_wag_selected(self):
        """When two WAGs are closed, the one with the more recent visits is chosen."""
        access, wag_old = make_access_and_wag()
        wa_old = make_was(wag_old, access, inaccessible=1, reached=5)
        make_visit(access, work_area=wa_old, visit_date=OUT_OF_PERIOD)

        make_closed_wag(access, reached=6)  # IN_PERIOD visits → selected as most recent

        self.assert_compute_result(access, sample=6, value=0.0)  # wag_recent has no inaccessible WAs

    def test_fewer_than_5_was_returns_insufficient_data(self):
        access = OpportunityAccessFactory()
        make_closed_wag(access, reached=4)
        self.assert_insufficient_run(access)

    def test_wag_closed_after_period_end_returns_insufficient_data(self):
        """A fully closed WAG whose last visit is after period_end must return N/A."""
        access = OpportunityAccessFactory()
        make_closed_wag(access, reached=5, visit_date=AFTER_PERIOD)
        self.assert_insufficient_data(access)


@pytest.mark.django_db
class TestVaccineRate(BaseIndicatorTest):
    calc = VaccineRate()

    @pytest.mark.parametrize(
        "given, not_given, expected_value, in_range",
        [
            (7, 3, 70.0, True),  # 70% — above 58% threshold
            (0, 10, 0.0, False),  # 0% — below threshold
            (10, 0, 100.0, True),  # 100% — no upper bound
        ],
        ids=["above_threshold", "zero_rate", "full_rate"],
    )
    def test_vaccine_rate_threshold(self, given, not_given, expected_value, in_range):
        access = OpportunityAccessFactory()
        for _ in range(given):
            _visit_with_vaccine(access, given=True)
        for _ in range(not_given):
            _visit_with_vaccine(access, given=False)
        self.assert_compute_result(access, sample=given + not_given, value=expected_value, in_range=in_range)

    def test_visits_after_period_end_excluded(self):
        access = OpportunityAccessFactory()
        _visit_with_vaccine(access, given=False, visit_date=AFTER_PERIOD)
        self.assert_insufficient_data(access)


@pytest.mark.django_db
class TestVaccineCardPhotoCompliance(BaseIndicatorTest):
    calc = VaccineCardPhotoCompliance()

    def test_unvaccinated_visits_not_in_denominator(self):
        access = OpportunityAccessFactory()
        _visit_with_vaccine(access, given=False)
        self.assert_insufficient_data(access)

    @pytest.mark.parametrize(
        "with_photo, without_photo, expected_value, in_range",
        [
            (5, 0, 100.0, True),  # 100% compliance
            (1, 4, 20.0, False),  # 20% compliance, below 38% threshold
        ],
        ids=["full_compliance", "below_threshold"],
    )
    def test_compliance_threshold(self, with_photo, without_photo, expected_value, in_range):
        access = OpportunityAccessFactory()
        for _ in range(with_photo):
            _visit_with_vaccine_card(access, has_photo=True)
        for _ in range(without_photo):
            _visit_with_vaccine_card(access, has_photo=False)
        self.assert_compute_result(access, sample=with_photo + without_photo, value=expected_value, in_range=in_range)

    def test_empty_photo_link_not_counted(self):
        access = OpportunityAccessFactory()
        make_visit(
            access,
            form_json={
                "form": {
                    "child_vaccine_group": {
                        "vaccines_ql": {"received_any_vaccine": YES},
                        "vaccine_photo_folder": {"photo_link_vaccine": ""},
                    },
                }
            },
        )
        self.assert_compute_result(access, value=0.0)

    def test_visits_after_period_end_excluded(self):
        access = OpportunityAccessFactory()
        _visit_with_vaccine_card(access, has_photo=False, visit_date=AFTER_PERIOD)
        self.assert_insufficient_data(access)


@pytest.mark.django_db
class TestMUACDistributionPatternIndex(BaseIndicatorTest):
    calc = MUACDistributionPatternIndex()

    def test_fewer_than_100_measurements_returns_insufficient_data(self):
        access = OpportunityAccessFactory()
        make_muac_visits(access, [12.0, 13.0, 14.0])
        self.assert_compute_result(access, sample=3)
        self.assert_insufficient_run(access)

    def test_out_of_range_measurements_excluded(self):
        access = OpportunityAccessFactory()
        make_muac_visits(access, [5.0, 25.0, 8.0])
        self.assert_insufficient_data(access)

    def test_realistic_distribution_passes_most_features(self):
        access = OpportunityAccessFactory()
        make_muac_visits(access)
        m = self.compute(access)
        assert m.sample_size == 100
        value = m.value
        assert value >= 5
        assert self.calc._in_range(value)

    def test_single_bin_distribution_fails_multiple_features(self):
        access = OpportunityAccessFactory()
        make_muac_visits(access, [13.0] * 100)
        value = self.compute(access).value
        assert value < 5
        assert not self.calc._in_range(value)

    def test_score_is_integer_between_0_and_6(self):
        access = OpportunityAccessFactory()
        make_muac_visits(access)
        value = self.compute(access).value
        assert isinstance(value, int)
        assert 0 <= value <= 6

    def test_visits_after_period_end_excluded(self):
        access = OpportunityAccessFactory()
        make_muac_visits(access, visit_date=AFTER_PERIOD)
        self.assert_insufficient_data(access)
