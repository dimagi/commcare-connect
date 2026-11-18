import datetime
import uuid
from collections import Counter
from functools import partial
from types import SimpleNamespace

from django.core.exceptions import PermissionDenied
from django.db.models import Count
from django.urls import reverse
from django.utils.timezone import now

from commcare_connect.opportunity.models import (
    DeliverUnit,
    DeliverUnitFlagRules,
    OpportunityClaimLimit,
    OpportunityVerificationFlags,
    UserVisit,
    VisitReviewStatus,
    VisitValidationStatus,
)
from commcare_connect.organization.models import UserOrganizationMembership
from commcare_connect.program.utils import is_opportunity_pm, opportunity_access_level_from_request
from commcare_connect.simulation.constants import DELIVER_UNIT_SLUGS
from commcare_connect.simulation.xforms import deliver_block, gps, submit_form
from commcare_connect.users.models import ConnectIDUserLink
from commcare_connect.utils.tables import PAGE_SIZE_OPTIONS

# The review pages act on at most one page of visits per request; approve_visits refuses more.
REVIEW_BATCH_SIZE = max(PAGE_SIZE_OPTIONS)

# Visit GPS points sit on a square grid of GPS_GRID_SIDE x GPS_GRID_SIDE points spaced well beyond the opportunity's
# "minimum distance between form locations", so clean visits never trip the location flag. A second pass lies
# half a step off the first; positions repeat only after two full grids.
GPS_ORIGIN = (-1.0, 36.0)
GPS_GRID_SIDE = 50
GPS_MIN_SPACING_M = 1000
METRES_PER_DEGREE = 111_320

# Forms are filled in during working hours, UTC, so a visit's date is the same in every view of it.
WORKDAY_START_HOUR = 7
WORKDAY_END_HOUR = 18
TOO_SHORT = datetime.timedelta(seconds=50)
# The longest a clean form takes beyond the deliver unit's minimum duration.
EXTRA_DURATION_MINUTES = (3, 20)
# A form reaches Connect this long, at most, after the worker finishes it.
SUBMIT_MARGIN = datetime.timedelta(minutes=10)

FIRST_NAMES = ("Amina", "Kofi", "Wanjiru", "Tendai", "Fatima", "Juma", "Esi", "Chidi", "Halima", "Baraka")
LAST_NAMES = ("Okafor", "Mensah", "Mwangi", "Banda", "Diallo", "Otieno", "Nkosi", "Abebe", "Kamau", "Sow")
JUSTIFICATION = "Checked with the worker; the visit took place as recorded."
REJECT_REASONS = (
    "Beneficiary could not confirm the visit.",
    "Form details do not match the household record.",
    "Visit was recorded outside the assigned area.",
)


class ReviewSteps:
    """Delivery forms and the review of the visits they create."""

    def deliver(
        self,
        opportunity,
        workers,
        *,
        per_worker,
        over_days=14,
        deliver_unit=None,
        duplicates=0,
        no_gps=0,
        too_short=0,
    ):
        """Submit ``per_worker`` deliver forms for each worker, spread over the last ``over_days`` days.

        ``duplicates``, ``no_gps`` and ``too_short`` are how many of each worker's forms trip that visit flag;
        whether a flag is raised still depends on the opportunity's verification settings, as in real use.
        ``deliver_unit`` is a deliver-unit slug; defaults to the first one. Returns the created ``UserVisit``s.
        """
        if duplicates + no_gps + too_short >= per_worker:
            raise ValueError(
                "Each worker needs at least one clean form: duplicates + no_gps + too_short < per_worker."
            )
        with self.step():
            app = opportunity.deliver_app
            unit = DeliverUnit.objects.select_related("payment_unit").get(
                app=app, slug=deliver_unit or DELIVER_UNIT_SLUGS[0]
            )
            min_duration = _min_duration(opportunity, unit)
            max_duration = min_duration + datetime.timedelta(minutes=EXTRA_DURATION_MINUTES[1])
            gps_spacing = _gps_spacing(opportunity)
            gps_index = UserVisit.objects.filter(opportunity=opportunity).count()
            xform_ids = []
            for worker in workers:
                username = _commcare_username(worker, app)
                times = self._visit_times(opportunity, worker, unit, per_worker, over_days, max_duration)
                knobs = self._assign_knobs(per_worker, duplicates, no_gps, too_short)
                entities = []
                for time_start, knob in zip(times, knobs):
                    if knob == "duplicate":
                        entity = self.rng.choice(entities)
                    else:
                        entity = self._new_entity()
                        entities.append(entity)
                    duration = (
                        TOO_SHORT if knob == "too_short" else min_duration + self._minutes(*EXTRA_DURATION_MINUTES)
                    )
                    location = None if knob == "no_gps" else self._gps(gps_index, gps_spacing)
                    gps_index += 1
                    # HQ forwards the form once the worker finishes it; the receiver checks limits against that day.
                    with self.at(time_start + duration):
                        form_json = submit_form(
                            app,
                            username,
                            deliver_block(unit.slug, *entity),
                            time_start=time_start,
                            duration=duration,
                            location=location,
                        )
                    xform_ids.append(form_json["id"])

            visits = list(UserVisit.objects.filter(xform_id__in=xform_ids).order_by("visit_date", "id"))
            self.log(f"Delivered {len(visits)} {unit.slug} visits for {len(workers)} workers on {opportunity.name}")
            self.link(f"{opportunity.name}: deliveries", _deliver_url(opportunity.organization, opportunity))
        return visits

    def review(self, opportunity, *, approve, reject=0.0):
        """Approve and reject shares of the pending visits, as ``nm_admin``."""
        if approve < 0 or reject < 0 or approve + reject > 1:
            raise ValueError("approve and reject are shares of the pending visits and must add up to at most 1.")
        _require_manual_review(opportunity)
        # approve_visits and reject_visits need opp_standard_access_required: a member of the NM organization.
        acting = _acting_request(self.nm_admin, opportunity.organization, opportunity)
        if not acting.opp_access_level.has_standard:
            raise PermissionDenied(f"{self.nm_admin} cannot review visits for {opportunity.organization}.")
        with self.step():
            pending = self._shuffled_visit_ids(
                opportunity, status__in=[VisitValidationStatus.pending, VisitValidationStatus.duplicate]
            )
            approve_count = round(len(pending) * approve)
            # Rounded together, so shares that add up to 1 leave nothing pending.
            reject_count = round(len(pending) * (approve + reject)) - approve_count
            to_approve = pending[:approve_count]
            to_reject = pending[approve_count : approve_count + reject_count]

            # The bulk Approve All / Reject All modals on the visit verification table, as the NM organization.
            browser = self.browser(self.nm_admin)
            org_slug = opportunity.organization.slug
            for batch in _batches(to_approve):
                browser.post(
                    _opportunity_url("approve_visits", org_slug, opportunity),
                    {"visit_ids[]": batch, "justification": JUSTIFICATION},
                    htmx=True,
                    succeeded=partial(_table_reloaded, batch, VisitValidationStatus.approved),
                )
            for batch in _batches(to_reject):
                browser.post(
                    _opportunity_url("reject_visits", org_slug, opportunity),
                    {"visit_ids[]": batch, "reason": self.rng.choice(REJECT_REASONS)},
                    htmx=True,
                    succeeded=partial(_table_reloaded, batch, VisitValidationStatus.rejected),
                )
            self.log(f"Reviewed {opportunity.name}: approved {len(to_approve)}, rejected {len(to_reject)}")

    def pm_review(self, opportunity, *, agree=1.0):
        """Agree with a share of the reviewed visits and disagree with the rest, as ``pm_admin``."""
        if not 0 <= agree <= 1:
            raise ValueError("agree is a share of the reviewed visits, between 0 and 1.")
        _require_manual_review(opportunity)
        pm_org = opportunity.program.organization if opportunity.program_id else None
        is_pm = is_opportunity_pm(_acting_request(self.pm_admin, pm_org, opportunity), opportunity)
        if not is_pm:
            raise PermissionDenied(f"{self.pm_admin} is not the program manager of {opportunity.name}.")
        with self.step():
            reviewed = self._shuffled_visit_ids(
                opportunity, status=VisitValidationStatus.approved, review_status=VisitReviewStatus.pending
            )
            agree_count = round(len(reviewed) * agree)
            # The Agree / Disagree buttons on each visit's details page, as the program manager organization.
            browser = self.browser(self.pm_admin)
            url = _opportunity_url("user_visit_review", pm_org.slug, opportunity)
            for review_status, visit_ids in (
                (VisitReviewStatus.agree, reviewed[:agree_count]),
                (VisitReviewStatus.disagree, reviewed[agree_count:]),
            ):
                for visit_id in visit_ids:
                    browser.post(
                        url,
                        {"pk": visit_id, "review_status": review_status.value},
                        htmx=True,
                        succeeded=partial(_reviewed, visit_id, review_status),
                    )
            disagree_count = len(reviewed) - agree_count
            self.log(f"PM reviewed {opportunity.name}: agreed {agree_count}, disagreed {disagree_count}")
            self.link(f"{opportunity.name}: PM review", _deliver_url(pm_org, opportunity))

    def _shuffled_visit_ids(self, opportunity, **filters):
        """The ids of the opportunity's visits matching ``filters``, in a seeded random order."""
        ids = list(
            UserVisit.objects.filter(opportunity=opportunity, **filters).order_by("id").values_list("id", flat=True)
        )
        self.rng.shuffle(ids)
        return ids

    def _visit_times(self, opportunity, worker, unit, count, over_days, max_duration):
        """``count`` form start times over the last ``over_days`` days that the receiver takes as ordinary visits.

        Days the worker's claim or the payment unit doesn't cover are skipped, each day stays under the daily
        limit, and the claim must have room for every visit, so none come out over-limit or trial.
        """
        payment_unit = unit.payment_unit
        claim_limit = (
            OpportunityClaimLimit.objects.select_related("opportunity_claim")
            .filter(
                opportunity_claim__opportunity_access__opportunity=opportunity,
                opportunity_claim__opportunity_access__user=worker,
                payment_unit=payment_unit,
            )
            .first()
        )
        if claim_limit is None:
            raise ValueError(f"{worker} has no claim limit for {payment_unit.name}; claim the opportunity first.")
        counted = _counted_visits(opportunity, worker, unit)
        left = claim_limit.max_visits - counted.count()
        if count > left:
            raise ValueError(
                f"{worker} has {left} of {claim_limit.max_visits} {payment_unit.name} visits left on their claim; "
                f"cannot deliver {count} more without going over the limit."
            )

        current = now()
        first_day = max(
            current.date() - datetime.timedelta(days=over_days - 1),
            opportunity.start_date,
            payment_unit.start_date or datetime.date.min,
        )
        last_day = min(
            current.date(),
            claim_limit.opportunity_claim.end_date,
            claim_limit.end_date or datetime.date.max,
        )
        days = [
            first_day + datetime.timedelta(days=offset)
            for offset in range((last_day - first_day).days + 1)
            if _latest_start(first_day + datetime.timedelta(days=offset), current, max_duration)
        ]
        room = _daily_room(counted, unit, days, count)
        if sum(room.values()) < count:
            raise ValueError(
                f"{worker} cannot deliver {count} {unit.slug} visits between {first_day} and {last_day} (the last "
                f"{over_days} days that the opportunity, payment unit and claim all cover) without going over the "
                f"daily limit of {payment_unit.max_daily}; raise over_days."
            )
        chosen = []
        for _ in range(count):
            day = self.rng.choice([day for day, left in room.items() if left])
            room[day] -= 1
            chosen.append(day)
        return sorted(self._time_on(day, current, max_duration) for day in chosen)

    def _time_on(self, day, current, max_duration):
        earliest = _at(day, datetime.time(WORKDAY_START_HOUR))
        latest = _latest_start(day, current, max_duration)
        return earliest + datetime.timedelta(seconds=self.rng.randrange(int((latest - earliest).total_seconds())))

    def _assign_knobs(self, count, duplicates, no_gps, too_short):
        """Which flag each form trips, in time order. A duplicate is never first, so it has a visit to repeat."""
        knobs = [None] * count
        later = self.rng.sample(range(1, count), duplicates) if duplicates else []
        for index in later:
            knobs[index] = "duplicate"
        free = [index for index in range(count) if knobs[index] is None]
        for index, knob in zip(
            self.rng.sample(free, no_gps + too_short), ["no_gps"] * no_gps + ["too_short"] * too_short
        ):
            knobs[index] = knob
        return knobs

    def _new_entity(self):
        entity_id = str(uuid.UUID(int=self.rng.getrandbits(128), version=4))
        return entity_id, f"{self.rng.choice(FIRST_NAMES)} {self.rng.choice(LAST_NAMES)}"

    def _gps(self, index, spacing):
        grid, cell = divmod(index, GPS_GRID_SIDE**2)
        row, column = divmod(cell, GPS_GRID_SIDE)
        shift = spacing / 2 if grid % 2 else 0
        jitter = spacing / 20
        lat = GPS_ORIGIN[0] + row * spacing + shift + self.rng.uniform(-jitter, jitter)
        lon = GPS_ORIGIN[1] + column * spacing + shift + self.rng.uniform(-jitter, jitter)
        return gps(lat, lon)

    def _minutes(self, low, high):
        return datetime.timedelta(minutes=self.rng.randint(low, high))


def _commcare_username(worker, app):
    """The HQ mobile-worker username the worker's phone submits forms as."""
    links = ConnectIDUserLink.objects.filter(user=worker).order_by("id")
    link = links.filter(commcare_username__endswith=f"@{app.cc_domain}.commcarehq.org").first() or links.first()
    if link is None:
        raise ValueError(f"{worker} has no ConnectID link to CommCare; claim the opportunity first.")
    return link.commcare_username


def _min_duration(opportunity, unit):
    rules = DeliverUnitFlagRules.objects.filter(opportunity=opportunity, deliver_unit=unit).first()
    return datetime.timedelta(minutes=rules.duration if rules else 0)


def _gps_spacing(opportunity):
    """Grid spacing in degrees: three times the location-flag distance, so jittered and offset points stay clear."""
    flags = OpportunityVerificationFlags.objects.filter(opportunity=opportunity).first()
    threshold = flags.location if flags else 0
    return max(3 * threshold, GPS_MIN_SPACING_M) / METRES_PER_DEGREE


def _latest_start(day, current, max_duration):
    """The latest working-hour start on ``day`` whose form is finished and submitted that day and before now."""
    latest = min(
        _at(day, datetime.time(WORKDAY_END_HOUR)),
        _at(day, datetime.time.max) - max_duration - SUBMIT_MARGIN,
        current - max_duration - SUBMIT_MARGIN,
    )
    return latest if latest > _at(day, datetime.time(WORKDAY_START_HOUR)) else None


def _at(day, time):
    return datetime.datetime.combine(day, time, tzinfo=datetime.UTC)


def _counted_visits(opportunity, worker, unit):
    """The worker's visits the receiver counts against the claim and daily limits."""
    return UserVisit.objects.filter(opportunity=opportunity, user=worker, deliver_unit=unit).exclude(
        status__in=[VisitValidationStatus.over_limit, VisitValidationStatus.trial]
    )


def _daily_room(counted, unit, days, count):
    """How many more visits each day takes before the payment unit's daily limit marks them over-limit."""
    max_daily = unit.payment_unit.max_daily
    if max_daily is None:
        return dict.fromkeys(days, count)
    per_day = counted.filter(visit_date__date__in=days).values_list("visit_date__date").annotate(count=Count("id"))
    used = Counter(dict(per_day))
    return {day: max(max_daily - used[day], 0) for day in days}


def _require_manual_review(opportunity):
    # The review pages refuse opportunities that verify visits automatically (require_manual_visit_verification).
    if opportunity.automatic_visit_verification:
        raise ValueError(f"{opportunity.name} verifies visits automatically; there is nothing to review by hand.")


def _acting_request(user, org, opportunity):
    """What the review pages' access rules read off the request when ``user`` works in ``org``."""
    request = SimpleNamespace(
        user=user,
        org=org,
        org_membership=UserOrganizationMembership.objects.filter(user=user, organization=org).first(),
    )
    request.opp_access_level = opportunity_access_level_from_request(request, opportunity)
    return request


def _batches(ids):
    for start in range(0, len(ids), REVIEW_BATCH_SIZE):
        yield ids[start : start + REVIEW_BATCH_SIZE]


def _table_reloaded(visit_ids, status, response):
    # approve_visits and reject_visits answer success by asking the page to reload the table, and refusals with
    # 400, 403 or 404. They also answer success when they filter every visit out, so success is the visits now
    # carrying the status too.
    return (
        response.status_code == 200
        and response.headers.get("HX-Trigger") == "reload_table"
        and not UserVisit.objects.filter(pk__in=visit_ids).exclude(status=status).exists()
    )


def _reviewed(visit_id, review_status, response):
    # user_visit_review answers 200 whether or not it recorded the review (it ignores anyone but the program
    # manager), so success is the visit now carrying the review.
    return response.status_code == 200 and UserVisit.objects.filter(pk=visit_id, review_status=review_status).exists()


def _opportunity_url(name, org_slug, opportunity):
    return reverse(f"opportunity:{name}", args=(org_slug, opportunity.opportunity_id))


def _deliver_url(org, opportunity):
    return _opportunity_url("worker_deliver", org.slug, opportunity)
