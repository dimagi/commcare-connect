"""Local-only tooling: fake CommCare HQ form submissions for an opportunity's workers.

Backs the opportunity "Test submissions" page. Forms are built here and run through the real form
processor, so they record exactly what an HQ-forwarded form would. Never enabled outside local
development: see ``commcare_connect.utils.local_testing``.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from uuid import uuid4

from django.db import transaction
from django.db.models import Q
from django.utils.timezone import now

from commcare_connect.form_receiver.const import CCC_LEARN_XMLNS
from commcare_connect.form_receiver.exceptions import ProcessingError
from commcare_connect.form_receiver.processor import process_xform
from commcare_connect.form_receiver.serializers import XFormSerializer
from commcare_connect.form_receiver.submission_simulator_types import SubmissionOverrides, WorkerStep
from commcare_connect.opportunity.models import (
    Assessment,
    CommCareApp,
    CompletedModule,
    DeliverUnit,
    LearnModule,
    Opportunity,
    OpportunityAccess,
    OpportunityClaim,
    OpportunityClaimLimit,
    PaymentUnit,
    UserInvite,
    UserVisit,
)
from commcare_connect.users.helpers import (
    accept_opportunity_invite,
    has_hq_user_link,
    mark_learning_started,
    save_hq_user_link,
)
from commcare_connect.users.models import ConnectIDUserLink, User

# timeEnd = timeStart + this, so the form-duration flag sees a plausible visit
FORM_DURATION = timedelta(minutes=5)
RECENT_ENTITY_LIMIT = 20
# How many of a worker's latest visits are scanned for distinct entities
RECENT_ENTITY_SCAN = 200


@dataclass
class WorkerRow:
    access: OpportunityAccess
    deliver_username: str | None
    learn_username: str | None
    # (entity_id, entity_name) of the worker's latest visits, for re-submitting an existing entity
    recent_entities: list[tuple[str, str]]
    # What the worker's row button does next; see get_next_worker_step
    next_step: WorkerStep | None


def get_worker_rows(opportunity: Opportunity) -> list[WorkerRow]:
    # A few queries per worker; fine for a local page over a dev database
    accesses = (
        OpportunityAccess.objects.filter(opportunity=opportunity)
        .select_related("user", "opportunity__deliver_app", "opportunity__learn_app")
        .order_by("user__name")
    )
    return [get_worker_row(access) for access in accesses]


def get_worker_row(access: OpportunityAccess) -> WorkerRow:
    opportunity = access.opportunity
    return WorkerRow(
        access=access,
        deliver_username=get_commcare_username(access.user, opportunity.deliver_app),
        learn_username=get_commcare_username(access.user, opportunity.learn_app),
        recent_entities=_recent_entities(access),
        next_step=get_next_worker_step(access),
    )


@dataclass
class ClaimOutcome:
    error: str | None = None
    claim_created: bool = False
    created_limits: list[OpportunityClaimLimit] = field(default_factory=list)
    # Units the remaining budget couldn't fund, so the worker has no limit (and can't deliver) for them
    skipped_payment_units: list[PaymentUnit] = field(default_factory=list)


def claim_for_worker(access: OpportunityAccess) -> ClaimOutcome:
    """Claim the opportunity for a worker the way the mobile claim endpoint does, or, for a worker
    who already claimed, add the claim limits they are missing (as adding a payment unit does).

    Also links the worker to the deliver app's domain, as the endpoint does, without creating the HQ user.
    """
    opportunity = access.opportunity
    with transaction.atomic():
        claim = OpportunityClaim.objects.filter(opportunity_access=access).first()
        claim_created = claim is None
        if claim_created:
            # The endpoint's budget check multiplies by max_total, so it 500s on a unit without one
            if unbounded := list(opportunity.paymentunit_set.filter(max_total__isnull=True)):
                names = ", ".join(unit.name for unit in unbounded)
                return ClaimOutcome(error=f"Payment unit(s) without a max total, so it can't be claimed: {names}.")
            # Locked so a concurrent claim can't pass the budget check too, as in the endpoint
            locked = Opportunity.objects.select_for_update().get(pk=opportunity.pk)
            if locked.remaining_budget < locked.budget_per_user():
                return ClaimOutcome(error="Opportunity is full: its remaining budget can't fund another worker.")
            if locked.has_ended:
                return ClaimOutcome(error="Opportunity has ended.")
            claim = OpportunityClaim.objects.create(opportunity_access=access, end_date=locked.end_date)

        existing_limit_ids = set(
            OpportunityClaimLimit.objects.filter(opportunity_claim=claim).values_list("pk", flat=True)
        )
        OpportunityClaimLimit.create_claim_limits(opportunity, claim)
        limits = list(OpportunityClaimLimit.objects.filter(opportunity_claim=claim).select_related("payment_unit"))
        if opportunity.deliver_app:
            _link_worker(access.user, opportunity.deliver_app.cc_domain, opportunity)

    funded_unit_ids = {limit.payment_unit_id for limit in limits}
    return ClaimOutcome(
        claim_created=claim_created,
        created_limits=[limit for limit in limits if limit.pk not in existing_limit_ids],
        skipped_payment_units=[
            unit for unit in opportunity.paymentunit_set.order_by("pk") if unit.pk not in funded_unit_ids
        ],
    )


NO_INVITE_ERROR = "This worker has no invite, so accepting it would fail."


def accept_invite_for_worker(access: OpportunityAccess) -> str | None:
    """Accept the worker's invite the way the invite link does. Returns an error, or None on success."""
    if not UserInvite.objects.filter(opportunity_access=access).exists():
        # The invite link's view would fail here too, so change nothing
        return NO_INVITE_ERROR
    accept_opportunity_invite(access)
    return None


def start_learning_for_worker(access: OpportunityAccess) -> str | None:
    """Start the learn app for a worker the way ``start_learn_app`` does, minus creating the HQ user:
    link the worker to the learn app's domain and mark learning started (and the invite accepted).
    Returns an error, or None on success.
    """
    if not UserInvite.objects.filter(opportunity_access=access).exists():
        return NO_INVITE_ERROR
    opportunity = access.opportunity
    with transaction.atomic():
        if opportunity.learn_app:
            _link_worker(access.user, opportunity.learn_app.cc_domain, opportunity)
        mark_learning_started(access)
    return None


def get_next_worker_step(access: OpportunityAccess) -> WorkerStep | None:
    """The next step a worker needs before delivering, or None when they are ready to deliver."""
    if not access.accepted:
        return WorkerStep.ACCEPT
    claim = OpportunityClaim.objects.filter(opportunity_access=access).first()
    if claim is None:
        # A worker who has claimed is past learning, even if no start was recorded
        return WorkerStep.LEARN if access.date_learn_started is None else WorkerStep.CLAIM
    unlimited_units = access.opportunity.paymentunit_set.exclude(opportunityclaimlimit__opportunity_claim=claim)
    # Only a limit the remaining budget can fund is a step; create_claim_limits skips the rest
    remaining_budget = access.opportunity.remaining_budget
    fundable = any(
        OpportunityClaimLimit._max_funded_visits_for_worker(unit, remaining_budget) >= 1 for unit in unlimited_units
    )
    return WorkerStep.LIMITS if fundable else None


def get_commcare_username(user: User, app: CommCareApp | None) -> str | None:
    """The worker's CommCare username on the app's domain, as the processor's ``get_user`` matches it.

    Matched on the username suffix rather than ``domain``/``hq_server``: links made by
    ``CreateUserLinkView`` leave those empty. ``None`` when the opportunity has no such app.
    """
    if app is None:
        return None
    link = (
        ConnectIDUserLink.objects.filter(user=user, commcare_username__iendswith=f"@{app.cc_domain}.commcarehq.org")
        .annotate(on_app_server=Q(hq_server_id=app.hq_server_id))
        .order_by("-on_app_server", "pk")
        .first()
    )
    return link.commcare_username if link else None


def build_deliver_form(
    app: CommCareApp, username: str, deliver_unit: DeliverUnit, overrides: SubmissionOverrides
) -> dict:
    entity_id = overrides.entity_id or str(uuid4())
    entity_name = overrides.entity_name or f"Test entity {entity_id[:8]}"
    # One visit per block: a form may not repeat (deliver unit, entity), so blocks after the first
    # get their own entity derived from the chosen one
    blocks = [
        {
            "@xmlns": CCC_LEARN_XMLNS,
            "@id": deliver_unit.slug,
            "name": deliver_unit.name,
            "entity_id": entity_id if n == 1 else f"{entity_id}-{n}",
            "entity_name": entity_name if n == 1 else f"{entity_name} {n}",
        }
        for n in range(1, overrides.deliver_block_count + 1)
    ]
    return _build_form(app, username, _keyed_blocks("deliver", blocks), overrides)


def build_learn_form(
    app: CommCareApp, username: str, modules: Sequence[LearnModule], overrides: SubmissionOverrides
) -> dict:
    blocks = [
        {
            "@xmlns": CCC_LEARN_XMLNS,
            "@id": module.slug,
            "name": module.name,
            "description": module.description,
            "time_estimate": str(module.time_estimate),
        }
        for module in modules
    ]
    return _build_form(app, username, _keyed_blocks("module", blocks), overrides)


def build_assessment_form(app: CommCareApp, username: str, score: int, overrides: SubmissionOverrides) -> dict:
    block = {"@xmlns": CCC_LEARN_XMLNS, "@id": "assessment", "user_score": str(score)}
    return _build_form(app, username, _keyed_blocks("assessment", [block]), overrides)


@dataclass
class SubmissionOutcome:
    payload: dict
    error: str | None = None
    visits: list[UserVisit] = field(default_factory=list)
    completed_modules: list[CompletedModule] = field(default_factory=list)
    assessments: list[Assessment] = field(default_factory=list)
    completed_learn_date: datetime | None = None
    # The processor finds the opportunity by app, so a form can land on another active opportunity
    # that uses the same app
    other_opportunities: list[Opportunity] = field(default_factory=list)

    @property
    def recorded_nothing(self) -> bool:
        # What the processor does with a form for an app no active opportunity uses: accept it silently
        return not (self.error or self.visits or self.completed_modules or self.assessments)


def submit_test_form(payload: dict, app: CommCareApp, access: OpportunityAccess) -> SubmissionOutcome:
    """Run a payload through the form processor the way the receiver does, and report what it recorded.

    Calls ``process_xform`` directly, skipping the receiver view and its OAuth lookup. Errors other
    than ``ProcessingError`` propagate, as they would to HQ.
    """
    serializer = XFormSerializer(data=payload)
    if not serializer.is_valid():
        return SubmissionOutcome(payload=payload, error=str(serializer.errors))
    xform = serializer.save()
    try:
        # A savepoint, so a rejected form rolls back alone, like the receiver's 400
        with transaction.atomic():
            process_xform(xform, app.hq_server)
    except ProcessingError as e:
        return SubmissionOutcome(payload=payload, error=str(e))

    access.refresh_from_db(fields=["completed_learn_date"])
    visits = list(UserVisit.objects.filter(xform_id=xform.id).select_related("deliver_unit", "user"))
    completed_modules = list(CompletedModule.objects.filter(xform_id=xform.id).select_related("module"))
    assessments = list(Assessment.objects.filter(xform_id=xform.id))
    other_opportunity_ids = {
        record.opportunity_id
        for record in [*visits, *completed_modules, *assessments]
        if record.opportunity_id != access.opportunity_id
    }
    return SubmissionOutcome(
        payload=payload,
        visits=visits,
        completed_modules=completed_modules,
        assessments=assessments,
        completed_learn_date=access.completed_learn_date,
        other_opportunities=list(Opportunity.objects.filter(pk__in=other_opportunity_ids)),
    )


def _link_worker(user: User, domain: str, opportunity: Opportunity) -> None:
    # create_hq_user_and_link, without asking HQ to create the user first
    if not has_hq_user_link(user, domain, opportunity.hq_server):
        save_hq_user_link(user, domain, opportunity.hq_server)


def _recent_entities(access: OpportunityAccess) -> list[tuple[str, str]]:
    visits = (
        UserVisit.objects.filter(opportunity_access=access, entity_id__isnull=False)
        .order_by("-visit_date")
        .values_list("entity_id", "entity_name")[:RECENT_ENTITY_SCAN]
    )
    entities = {}
    for entity_id, entity_name in visits:
        entities.setdefault(entity_id, entity_name)
    return list(entities.items())[:RECENT_ENTITY_LIMIT]


def _keyed_blocks(kind: str, blocks: Sequence[dict]) -> dict:
    # Each block under its own parent key, like a repeat group: the processor's JSONPath
    # lookups expect every match to be a dict, so a list of blocks would not be found.
    return {f"{kind}_{n}": {kind: dict(block)} for n, block in enumerate(blocks, start=1)}


def _build_form(app: CommCareApp, username: str, blocks: dict, overrides: SubmissionOverrides) -> dict:
    """The envelope of an HQ ConnectFormRepeater payload. No attachments, so nothing is fetched from HQ."""
    xform_id = str(uuid4())
    time_start = overrides.visit_time or now()
    metadata = {
        "username": username,
        "timeStart": time_start.isoformat(),
        "timeEnd": (time_start + FORM_DURATION).isoformat(),
        "location": _location(overrides),
        "app_build_version": 1,
        "instanceID": xform_id,
        "deviceID": "connect-test-submissions",
    }
    return {
        "domain": app.cc_domain,
        "app_id": app.cc_app_id,
        "id": xform_id,
        "build_id": "test-submission",
        "received_on": now().isoformat(),
        "metadata": metadata,
        "form": {
            "@xmlns": "http://commcareconnect.com/test-submission",
            "@name": "Test submission",
            "meta": metadata,
            **blocks,
        },
    }


def _location(overrides: SubmissionOverrides) -> str | None:
    if overrides.latitude is None or overrides.longitude is None:
        return None
    # HQ's "lat lon altitude accuracy" string
    return f"{overrides.latitude} {overrides.longitude} 0 10"
