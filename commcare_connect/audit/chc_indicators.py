from __future__ import annotations

import logging

from django.db.models import Count, F, IntegerField, Max, Q, Sum, Value
from django.db.models.fields.json import KeyTextTransform
from django.db.models.functions import Cast, NullIf

from commcare_connect.audit.calculations import AuditCalculation, Measurement, register_calculation
from commcare_connect.microplanning.models import WorkArea, WorkAreaGroup, WorkAreaStatus
from commcare_connect.opportunity.models import UserVisit

logger = logging.getLogger(__name__)

CLOSED_STATUSES = [
    WorkAreaStatus.EXPECTED_VISIT_REACHED,
    WorkAreaStatus.EXCLUDED,
    WorkAreaStatus.INACCESSIBLE,
]


TERMINAL_STATUSES = [
    WorkAreaStatus.INACCESSIBLE,
    WorkAreaStatus.EXPECTED_VISIT_REACHED,
]

# form_json field path constants — paths are relative to form_json["form"], using __ for nesting
GENDER_FIELD = "additional_case_info__childs_gender"  # values: "female" / "male"
FEMALE = "female"
AGE_FIELD = "additional_case_info__childs_age_in_months"  # string months, e.g. "12"

MUAC_MEASUREMENT_FIELD = (
    "muac_group__muac_display_group_2__muac_colour_display__soliciter_muac_cm"  # float cm (string)
)
MUAC_PHOTO_LINK_FIELD = "muac_group__muac_photo_link"  # URL (non-empty = photo taken)
MUAC_CONSENT_FIELD = "muac_group__muac_consent_group__muac_consent"

VACCINE_FIELD = "child_vaccine_group__vaccines_ql__received_any_vaccine"  # values: "yes" / "no"
YES = "yes"
# URL (non-empty = photo taken)
VACCINE_CARD_LINK_FIELD = "child_vaccine_group__vaccine_photo_folder__photo_link_vaccine"

MUAC_BIN_EDGES = [9.5, 10.5, 11.5, 12.5, 13.5, 14.5, 15.5, 16.5, 17.5, 18.5, 19.5, 20.5, 21.5]
AGE_HEAPING_VALUES = ["12", "24", "36", "48"]
MAX_VISITS_PER_BUILDING = 12  # threshold above which a WA is flagged as "camping"
SERVICE_DELIVERY_SLUG = "services_delivery_unit"


def _percent(numerator: int, denominator: int) -> float:
    return round(numerator / denominator * 100, 2)


def _q_link_present(form_field) -> Q:
    """Return a Q that matches when a form URL link field is non-null and non-empty."""
    path = f"form_json__form__{form_field}"
    return Q(**{f"{path}__isnull": False}) & ~Q(**{path: ""})


def _json_int(form_field) -> Cast:
    """Cast a __-delimited form_json field to an integer for numeric comparison.

    The form stores numbers as JSON strings (e.g. "12"), so a raw __gt lookup would
    compare lexicographically ("12" < "6"). KeyTextTransform extracts the value as text
    (->>), NullIf maps "" to NULL so the cast skips blanks, then Cast yields a real int.
    """
    expr = "form_json"
    for key in ("form", *form_field.split("__")):
        expr = KeyTextTransform(key, expr)
    return Cast(NullIf(expr, Value("")), IntegerField())


def _find_active_wag(opportunity_access, period_end) -> WorkAreaGroup | None:
    """Return the active WAG with the most recent visit (up to period_end)
    among WAGs that still have non-terminal WAs."""
    active_wag_ids = (
        WorkArea.objects.filter(
            opportunity_access=opportunity_access,
            work_area_group__isnull=False,
        )
        .exclude(status__in=TERMINAL_STATUSES)
        .values_list("work_area_group_id", flat=True)
        .distinct()
    )

    return (
        WorkAreaGroup.objects.filter(id__in=active_wag_ids)
        .annotate(
            last_visit=Max(
                "workarea__uservisit__visit_date",
                filter=Q(workarea__uservisit__visit_date__date__lte=period_end)
                & Q(workarea__uservisit__deliver_unit__slug=SERVICE_DELIVERY_SLUG),
            )
        )
        .filter(last_visit__isnull=False)
        .order_by("-last_visit")
        .first()
    )


def _find_last_closed_wag(opportunity_access, period_end) -> WorkAreaGroup | None:
    """Return the most recently closed WAG (all WAs terminal) with visits up to period_end."""
    return (
        WorkAreaGroup.objects.filter(
            workarea__opportunity_access=opportunity_access,
        )
        .annotate(
            total=Count("workarea", distinct=True),
            closed_count=Count(
                "workarea",
                filter=Q(workarea__status__in=CLOSED_STATUSES),
                distinct=True,
            ),
            last_visit=Max(
                "workarea__uservisit__visit_date",
                filter=Q(workarea__uservisit__visit_date__date__lte=period_end)
                & Q(workarea__uservisit__deliver_unit__slug=SERVICE_DELIVERY_SLUG),
            ),
        )
        .filter(total=F("closed_count"), last_visit__isnull=False)
        .order_by("-last_visit")
        .first()
    )


@register_calculation
class CampingRatio(AuditCalculation):
    """Detect inflated visit reporting within a Work Area's building count.
    Flags if any WA has >MAX_VISITS_PER_BUILDING visits per building in the report week.
    Returns count of camping WAs; upper_bound=0 means any camping WA flags the FLW.
    """

    name = "camping_ratio"
    label = "Camping (Visit:Building Ratio)"
    tooltip = "Flagged if visits exceed 12 per building in any single Work Area in the past week."
    min_sample_size = 1
    upper_bound = 0

    def compute(self, opportunity_access, period_start, period_end):
        wa_visit_counts = (
            UserVisit.objects.filter(
                opportunity_access=opportunity_access,
                visit_date__date__range=(period_start, period_end),
                work_area__isnull=False,
                work_area__building_count__gt=0,
                deliver_unit__slug=SERVICE_DELIVERY_SLUG,
            )
            .values("work_area_id", "work_area__building_count")
            .annotate(visit_count=Count("id"))
        )

        total_evaluated = len(wa_visit_counts)
        camping_count = sum(
            1
            for row in wa_visit_counts
            if row["visit_count"] > MAX_VISITS_PER_BUILDING * row["work_area__building_count"]
        )
        return Measurement(camping_count, total_evaluated)


@register_calculation
class GenderRatioDeviation(AuditCalculation):
    """Detect gender imbalance suggesting selective visit recording.
    Percent = female visits / last 97 completed visits * 100.
    Flags if female percent < 40 or > 60 (10% max deviation from 50/50 at 95% confidence).
    """

    name = "gender_ratio_deviation"
    label = "Gender Ratio Deviation"
    tooltip = "Flagged if female or male ratio falls below 40% or exceeds 60%."
    is_percentage = True
    min_sample_size = 97
    lower_bound = 40
    upper_bound = 60

    def compute(self, opportunity_access, period_start, period_end):
        result = UserVisit.objects.filter(
            opportunity_access=opportunity_access,
            visit_date__date__range=(period_start, period_end),
            deliver_unit__slug=SERVICE_DELIVERY_SLUG,
        ).aggregate(
            total=Count("id"),
            female=Count("id", filter=Q(**{f"form_json__form__{GENDER_FIELD}": FEMALE})),
        )
        total = result["total"]
        if not total:
            return Measurement(None, 0)
        return Measurement(_percent(result["female"], total), total)


@register_calculation
class MUACPhotoCompliance(AuditCalculation):
    """Detect missing MUAC measurement photos for eligible children.
    Denominator: last 70 visits where child is >6 months old (regardless of consent).
    Numerator: visits where muac_photo_link is non-empty.
    Flags if compliance < 72% (p=0.80, n=70, one-sided 95% CI lower bound).
    """

    name = "muac_photo_compliance"
    label = "MUAC Photo Compliance"
    tooltip = "Flagged if MUAC photo taken in fewer than 72% of eligible visits (children ≥6 months)."
    is_percentage = True
    min_sample_size = 70
    lower_bound = 72

    def compute(self, opportunity_access, period_start, period_end):
        result = (
            UserVisit.objects.filter(
                opportunity_access=opportunity_access,
                visit_date__date__range=(period_start, period_end),
                deliver_unit__slug=SERVICE_DELIVERY_SLUG,
            )
            .annotate(age_months=_json_int(AGE_FIELD))
            .filter(age_months__gt=6)
            .aggregate(
                total=Count("id"),
                with_photo=Count("id", filter=_q_link_present(MUAC_PHOTO_LINK_FIELD)),
            )
        )
        total = result["total"]
        if not total:
            return Measurement(None, 0)
        return Measurement(_percent(result["with_photo"], total), total)


@register_calculation
class AgeHeaping(AuditCalculation):
    """Detect rounding/shortcut age entry at exact whole-year values.
    Flags when visits with childs_age_in_month in (12, 24, 36, 48) exceed 19%
    of the last 97 visits.
    Threshold: p=0.134, n=97, one-sided 95% CI upper bound.
    """

    name = "age_heaping"
    label = "Age Heaping"
    tooltip = "Flagged if more than 19% of recorded ages are exactly 12, 24, 36, or 48 months."
    is_percentage = True
    min_sample_size = 97
    upper_bound = 19

    def compute(self, opportunity_access, period_start, period_end):
        result = UserVisit.objects.filter(
            opportunity_access=opportunity_access,
            visit_date__date__range=(period_start, period_end),
            deliver_unit__slug=SERVICE_DELIVERY_SLUG,
            **{f"form_json__form__{AGE_FIELD}__isnull": False},
        ).aggregate(
            total=Count("id"),
            heaped=Count("id", filter=Q(**{f"form_json__form__{AGE_FIELD}__in": AGE_HEAPING_VALUES})),
        )
        total = result["total"]
        if not total:
            return Measurement(None, 0)
        return Measurement(_percent(result["heaped"], total), total)


@register_calculation
class WACoverageToVisitRatio(AuditCalculation):
    """Detect imbalance between work area coverage progress and visit progress.

    Ratio = (VISITED WAs / eligible WAs) / (actual visits / expected visits).
    Eligible WAs exclude only those marked EXCLUDED; INACCESSIBLE WAs are included.
    Uses cumulative totals from campaign start, not just the report week.
    """

    name = "wa_coverage_to_visit_ratio"
    label = "WA Coverage to Visit Ratio"
    tooltip = "Flagged if ratio falls below 0.6 or exceeds 1.4."
    min_sample_size = 1
    lower_bound = 0.6
    upper_bound = 1.4

    def compute(self, opportunity_access, period_start, period_end):
        _eligible = ~Q(status__in=[WorkAreaStatus.EXCLUDED])
        wa_stats = WorkArea.objects.filter(opportunity_access=opportunity_access).aggregate(
            total_eligible=Count("id", filter=_eligible),
            visited_count=Count(
                "id", filter=Q(status__in=[WorkAreaStatus.VISITED, WorkAreaStatus.EXPECTED_VISIT_REACHED])
            ),
            expected_visits=Sum("expected_visit_count", filter=_eligible),
        )

        total_eligible = wa_stats["total_eligible"] or 0
        expected_visits = wa_stats["expected_visits"] or 0
        actual_visits = UserVisit.objects.filter(
            opportunity_access=opportunity_access,
            visit_date__date__lte=period_end,
            work_area__isnull=False,
            deliver_unit__slug=SERVICE_DELIVERY_SLUG,
        ).count()

        if not (total_eligible and expected_visits and actual_visits):
            return Measurement(None, 0)

        visited_count = wa_stats["visited_count"]
        coverage_ratio = visited_count / total_eligible
        visit_ratio = actual_visits / expected_visits
        return Measurement(
            coverage_ratio / visit_ratio,
            total_eligible,
            components=[
                {"numerator": visited_count, "denominator": total_eligible},
                {"numerator": actual_visits, "denominator": expected_visits},
            ],
        )


@register_calculation
class InaccessibleWARateEarlyWarning(AuditCalculation):
    """Detect emerging overuse of Inaccessible state mid-WAG.
    Evaluated against the FLW's current active WAG.
    Requires ≥5 WAs with terminal status before evaluating.
    """

    name = "inaccessible_wa_rate_early_warning"
    label = "Inaccessible WA Rate – Early Warning"
    tooltip = "Flagged if more than 25% of Work Areas in the current active WAG are marked Inaccessible."
    is_percentage = True
    min_sample_size = 5
    upper_bound = 25

    def compute(self, opportunity_access, period_start, period_end):
        active_wag = _find_active_wag(opportunity_access, period_end)
        if active_wag is None:
            return Measurement(None, 0)

        stats = WorkArea.objects.filter(work_area_group=active_wag, opportunity_access=opportunity_access).aggregate(
            total=Count("id"),
            terminal_count=Count("id", filter=Q(status__in=TERMINAL_STATUSES)),
            inaccessible_count=Count("id", filter=Q(status=WorkAreaStatus.INACCESSIBLE)),
        )

        if not stats["total"]:
            return Measurement(None, 0)

        # Rate is over all assigned WAs (total), but evaluation is gated on ≥5
        # terminal WAs — so the display denominator (total) differs from the
        # gating sample_size (terminal_count).
        return Measurement(
            value=_percent(stats["inaccessible_count"], stats["total"]),
            sample_size=stats["terminal_count"],
            denominator_override=stats["total"],
        )


@register_calculation
class InaccessibleWARateLastCompletedWAG(AuditCalculation):
    """Detect systematic overuse of Inaccessible state in the most recently completed WAG.
    Evaluated against the last completed WAG (all WAs terminal), identified by
    the latest visit date across its WAs.
    """

    name = "inaccessible_wa_rate_last_completed_wag"
    label = "Inaccessible WA Rate – Last Completed WAG"
    tooltip = "Flagged if more than 15% of Work Areas in the most recently closed WAG are marked Inaccessible."
    is_percentage = True
    min_sample_size = 5
    upper_bound = 15

    def compute(self, opportunity_access, period_start, period_end):
        last_wag = _find_last_closed_wag(opportunity_access, period_end)
        if last_wag is None:
            return Measurement(None, 0)

        stats = (
            WorkArea.objects.filter(
                work_area_group=last_wag,
                opportunity_access=opportunity_access,
            )
            .exclude(status=WorkAreaStatus.EXCLUDED)
            .aggregate(
                total=Count("id"),
                inaccessible_count=Count(
                    "id",
                    filter=Q(status=WorkAreaStatus.INACCESSIBLE),
                ),
            )
        )

        total = stats["total"]
        if total == 0:
            return Measurement(None, 0)

        return Measurement(_percent(stats["inaccessible_count"], total), total)


@register_calculation
class VaccineRate(AuditCalculation):
    """Detect gaps in vaccine question completion.
    Rate = visits where received_any_vaccine = yes / last 97 visits.
    Flags if rate < 58% (anchored to Gombe baseline 66%, one-sided 95% CI).
    """

    name = "vaccine_rate"
    label = "Vaccine Rate"
    tooltip = "Flagged if fewer than 58% of visits record at least one vaccine received."
    is_percentage = True
    min_sample_size = 97
    lower_bound = 58

    def compute(self, opportunity_access, period_start, period_end):
        result = UserVisit.objects.filter(
            opportunity_access=opportunity_access,
            visit_date__date__range=(period_start, period_end),
            deliver_unit__slug=SERVICE_DELIVERY_SLUG,
        ).aggregate(
            total=Count("id"),
            vaccinated=Count("id", filter=Q(**{f"form_json__form__{VACCINE_FIELD}": YES})),
        )
        total = result["total"]
        if not total:
            return Measurement(None, 0)
        return Measurement(_percent(result["vaccinated"], total), total)


@register_calculation
class VaccineCardPhotoCompliance(AuditCalculation):
    """Detect missing vaccine card photos for vaccinated children.

    Denominator: last 97 visits where received_any_vaccine = yes.
    Numerator: visits where photo_link_vaccine is non-empty.
    Flags if compliance < 38% (anchored to Kano card availability 46%, one-sided 95% CI).
    """

    name = "vaccine_card_photo_compliance"
    label = "Vaccine Card Photo Compliance"
    tooltip = "Flagged if vaccine card photo taken in fewer than 38% of visits where child was vaccinated."
    is_percentage = True
    min_sample_size = 97
    lower_bound = 38

    def compute(self, opportunity_access, period_start, period_end):
        result = UserVisit.objects.filter(
            opportunity_access=opportunity_access,
            visit_date__date__range=(period_start, period_end),
            deliver_unit__slug=SERVICE_DELIVERY_SLUG,
            **{f"form_json__form__{VACCINE_FIELD}": YES},
        ).aggregate(
            total=Count("id"),
            with_photo=Count("id", filter=_q_link_present(VACCINE_CARD_LINK_FIELD)),
        )
        total = result["total"]
        if not total:
            return Measurement(None, 0)
        return Measurement(_percent(result["with_photo"], total), total)


# ── MUAC distribution helpers (ported from MLFeatureAggregationReport.py) ────


def _muac_build_bins(measurements: list[float]) -> list[int]:
    # Count how many measurements fall into each MUAC range (bin).
    counts = []
    for i in range(len(MUAC_BIN_EDGES) - 1):
        lo, hi = MUAC_BIN_EDGES[i], MUAC_BIN_EDGES[i + 1]
        if i == len(MUAC_BIN_EDGES) - 2:
            counts.append(sum(1 for m in measurements if lo <= m <= hi))
        else:
            counts.append(sum(1 for m in measurements if lo <= m < hi))
    return counts


def _muac_increasing_to_peak(bin_counts, non_zero_indices, peak_index, wiggle) -> bool:
    # True if counts rise (at least somewhat steadily) as we approach the peak bin.
    if peak_index == 0 or peak_index not in non_zero_indices:
        return False
    peak_pos = non_zero_indices.index(peak_index)
    if peak_pos == 0:
        return False

    increasing_steps = big_decreases = 0
    for i in range(peak_pos):
        change = bin_counts[non_zero_indices[i + 1]] - bin_counts[non_zero_indices[i]]
        if change > 0:
            increasing_steps += 1
        if change < -wiggle:
            big_decreases += 1

    adequate = any(bin_counts[i] >= bin_counts[peak_index] * 0.25 for i in range(peak_index))
    return increasing_steps >= 1 and big_decreases == 0 and adequate


def _muac_decreasing_from_peak(bin_counts, non_zero_indices, peak_index, wiggle) -> bool:
    # True if counts fall (without big jumps back up) after the peak bin.
    if peak_index == len(bin_counts) - 1 or peak_index not in non_zero_indices:
        return False
    peak_pos = non_zero_indices.index(peak_index)
    if peak_pos == len(non_zero_indices) - 1:
        return False

    decreasing_steps = big_increases = 0
    for i in range(peak_pos, len(non_zero_indices) - 1):
        change = bin_counts[non_zero_indices[i + 1]] - bin_counts[non_zero_indices[i]]
        if change < 0:
            decreasing_steps += 1
        if change > wiggle:
            big_increases += 1

    return decreasing_steps >= 1 and big_increases == 0


def _muac_no_skipped_bins(bin_counts, total_count) -> bool:
    # True if there are no empty gaps between the significant (>2%) bins.
    if total_count <= 0:
        return True
    threshold = total_count * 0.02
    sig = [i for i, c in enumerate(bin_counts) if c >= threshold]
    if len(sig) <= 1:
        return True
    return all(c != 0 for c in bin_counts[sig[0] : sig[-1] + 1])  # noqa: E203


def _muac_no_plateau(bin_counts, max_count) -> bool:
    # True if there's no flat "plateau" at the top — 3+ consecutive high bins at similar counts looks suspicious.
    total_count = sum(bin_counts)
    if total_count == 0 or max_count == 0:
        return True

    threshold = 0.5 * max_count
    tolerance = total_count * 0.04
    high_bins = [(i, c) for i, c in enumerate(bin_counts) if c >= threshold]

    if len(high_bins) < 2:
        return True

    longest_plateau = 1
    plateau_start = 0
    for i in range(1, len(high_bins)):
        if high_bins[i][0] == high_bins[i - 1][0] + 1:
            segment = high_bins[plateau_start : i + 1]  # noqa: E203
            segment_counts = [x[1] for x in segment]
            if max(segment_counts) - min(segment_counts) <= tolerance:
                longest_plateau = max(longest_plateau, len(segment))
            else:
                plateau_start = i
        else:
            plateau_start = i

    return longest_plateau < 3  # 3+ consecutive similar high bins = suspicious


@register_calculation
class MUACDistributionPatternIndex(AuditCalculation):
    """Assess whether a FLW's MUAC distribution looks biologically realistic.
    Scores 6 boolean shape features of the histogram (0–6 total).
    Flags if fewer than 5 features pass. Requires ≥100 valid measurements.
    Features: increasing_to_peak, decreasing_from_peak, no_skipped_bins,
    no_plateau, bins_sufficient (≥5), peak_reasonable (≤42% concentration).
    """

    name = "muac_distribution_pattern_index"
    label = "MUAC Distribution Pattern Index (MDPI)"
    tooltip = "Flagged if fewer than 5 of 6 distribution shape tests pass."
    min_sample_size = 100
    lower_bound = 5

    def compute(self, opportunity_access, period_start, period_end):
        raw = UserVisit.objects.filter(
            opportunity_access=opportunity_access,
            visit_date__date__range=(period_start, period_end),
            deliver_unit__slug=SERVICE_DELIVERY_SLUG,
            **{f"form_json__form__{MUAC_MEASUREMENT_FIELD}__isnull": False},
        ).values_list(f"form_json__form__{MUAC_MEASUREMENT_FIELD}", flat=True)

        measurements = []
        out_of_range = []
        for v in raw:
            try:
                f = float(v)
                if 9.5 <= f <= 21.5:
                    measurements.append(f)
                else:
                    out_of_range.append(f)
            except (TypeError, ValueError):
                pass

        if out_of_range:
            logger.warning(
                "MUAC out-of-range values for opportunity_access=%s period=%s–%s: %d value(s) outside 9.5–21.5 cm: %s",
                opportunity_access.id,
                period_start,
                period_end,
                len(out_of_range),
                out_of_range[:10],
            )

        total = len(measurements)
        if total < self.min_sample_size:
            return Measurement(None, total)

        bin_counts = _muac_build_bins(measurements)
        max_count = max(bin_counts)
        peak_index = next(i for i, c in enumerate(bin_counts) if c == max_count)
        non_zero = [i for i, c in enumerate(bin_counts) if c > 0]
        wiggle = total * 0.02  # 2% of total measurements, used as a small noise tolerance in shape checks

        score = sum(
            [
                _muac_increasing_to_peak(bin_counts, non_zero, peak_index, wiggle),
                _muac_decreasing_from_peak(bin_counts, non_zero, peak_index, wiggle),
                _muac_no_skipped_bins(bin_counts, total),
                _muac_no_plateau(bin_counts, max_count),
                len(non_zero) >= 5,
                max_count / total <= 0.42,
            ]
        )
        return Measurement(score, total)
