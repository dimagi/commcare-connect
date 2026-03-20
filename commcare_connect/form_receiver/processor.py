import datetime
import logging
from functools import partial
from uuid import UUID

from django.db import transaction
from django.db.models import Count, Min, Q
from django.utils.timezone import now
from geopy.distance import distance
from jsonpath_ng import JSONPathError
from jsonpath_ng.ext import parse

from commcare_connect.commcarehq.models import HQServer
from commcare_connect.form_receiver.const import CCC_LEARN_XMLNS
from commcare_connect.form_receiver.exceptions import ProcessingError
from commcare_connect.form_receiver.serializers import XForm
from commcare_connect.microplanning.models import WorkArea
from commcare_connect.opportunity.models import (
    Assessment,
    AssignedTask,
    AssignedTaskStatus,
    CommCareApp,
    CompletedModule,
    CompletedWork,
    CompletedWorkStatus,
    DeliverUnit,
    DeliverUnitFlagRules,
    FormJsonValidationRules,
    LearnModule,
    Opportunity,
    OpportunityAccess,
    OpportunityClaim,
    OpportunityClaimLimit,
    OpportunityVerificationFlags,
    UserVisit,
    VisitReviewStatus,
    VisitValidationStatus,
)
from commcare_connect.opportunity.tasks import download_user_visit_attachments, notify_user_for_scored_assessment
from commcare_connect.opportunity.visit_import import update_payment_accrued_for_user
from commcare_connect.users.models import User
from commcare_connect.utils.lock import try_redis_lock

logger = logging.getLogger(__name__)

LEARN_MODULE_JSONPATH = parse("$..module")
TASK_MODULE_JSONPATH = parse("$..task")
ASSESSMENT_JSONPATH = parse("$..assessment")
DELIVER_UNIT_JSONPATH = parse("$..deliver")


def is_a_uuid(value):
    try:
        UUID(str(value))
        return True
    except ValueError:
        return False


def process_xform(xform: XForm, hq_server: HQServer):
    """Process a form received from CommCare HQ."""
    user = get_user(xform)

    opportunity = get_opportunity(xform.domain, hq_server, deliver_app_id=xform.app_id)
    if opportunity:
        app = opportunity.deliver_app
        process_deliver_form(user, xform, app, opportunity)

    opportunity = get_opportunity(xform.domain, hq_server, learn_app_id=xform.app_id)
    if opportunity:
        app = opportunity.learn_app
        process_learn_form(user, xform, app, opportunity)


def process_learn_form(user, xform: XForm, app: CommCareApp, opportunity: Opportunity):
    processors = [
        (LEARN_MODULE_JSONPATH, process_learn_modules),
        (ASSESSMENT_JSONPATH, process_assessments),
    ]
    for jsonpath, processor in processors:
        try:
            matches = _get_matching_blocks(jsonpath, xform)
            if matches:
                processor(user, xform, app, opportunity, matches)
        except JSONPathError as e:
            raise ProcessingError from e


def _get_matching_blocks(jsonpath, xform):
    return [match.value for match in jsonpath.find(xform.form) if match.value["@xmlns"] == CCC_LEARN_XMLNS]


def get_or_create_learn_module(app, module_data):
    module, _ = LearnModule.objects.get_or_create(
        app=app,
        slug=module_data["@id"],
        defaults=dict(
            name=module_data["name"],
            description=module_data["description"],
            time_estimate=module_data["time_estimate"],
        ),
    )
    return module


def process_learn_modules(user: User, xform: XForm, app: CommCareApp, opportunity: Opportunity, blocks: list[dict]):
    """Process learn modules from a form received from CommCare HQ.

    :param user: The user who submitted the form.
    :param xform: The deserialized form object.
    :param app: The CommCare app the form belongs to.
    :param opportunity: The opportunity the app belongs to.
    :param blocks: A list of learn module form blocks."""
    with transaction.atomic():
        access = OpportunityAccess.objects.get(user=user, opportunity=opportunity)
        completed_modules = []
        save_access = False
        for module_data in blocks:
            module = get_or_create_learn_module(app, module_data)
            completed_module = CompletedModule(
                user=user,
                module=module,
                opportunity=opportunity,
                opportunity_access=access,
                xform_id=xform.id,
                date=xform.metadata.timeEnd,
                duration=xform.metadata.duration,
                app_build_id=xform.build_id,
                app_build_version=xform.metadata.app_build_version,
            )
            completed_modules.append(completed_module)
            if not access.last_active or access.last_active < completed_module.date:
                access.last_active = completed_module.date
                save_access = True

        if completed_modules:
            CompletedModule.objects.bulk_create(completed_modules)
            update_completed_learn_date(access, save_access)


def process_task_modules(user: User, xform: XForm, app: CommCareApp, opportunity: Opportunity, blocks: list[dict]):
    """Process task modules from a form received from CommCare HQ."""
    with transaction.atomic():
        try:
            access = OpportunityAccess.objects.get(opportunity=opportunity, user=user)
        except OpportunityAccess.DoesNotExist:
            raise ProcessingError(f"User does not have access to opportunity {opportunity.name}")
        save_access = False

        updated_tasks = False

        for task_data in blocks:
            task_slug = task_data.get("@id")
            if not task_slug:
                continue

            try:
                assigned_task = (
                    AssignedTask.objects.select_for_update()
                    .filter(
                        task_type__app=app,
                        task_type__slug=task_slug,
                        opportunity_access=access,
                        xform_id=None,
                        status=AssignedTaskStatus.ASSIGNED,
                    )
                    .get()
                )
            except AssignedTask.DoesNotExist:
                continue

            assigned_task.xform_id = xform.id
            assigned_task.completed_at = xform.metadata.timeEnd
            assigned_task.duration = xform.metadata.duration
            assigned_task.app_build_id = xform.build_id
            assigned_task.app_build_version = xform.metadata.app_build_version
            assigned_task.status = AssignedTaskStatus.COMPLETED

            assigned_task.save(
                update_fields=[
                    "xform_id",
                    "completed_at",
                    "duration",
                    "app_build_id",
                    "app_build_version",
                    "status",
                ]
            )
            updated_tasks = True
            if not access.last_active or access.last_active < assigned_task.completed_at:
                access.last_active = assigned_task.completed_at
                save_access = True

        if updated_tasks and save_access:
            access.save(update_fields=["last_active"])


def update_completed_learn_date(access, save_access=False):
    if not access.completed_learn_date and access.learn_progress == 100.0:
        # Get the earliest completion date for each unique module
        earliest_dates = (
            CompletedModule.objects.filter(opportunity_access=access)
            .values("module")
            .annotate(earliest_date=Min("date"))
        )
        completed_learn_date = max(entry["earliest_date"] for entry in earliest_dates)
        access.completed_learn_date = completed_learn_date
        save_access = True

    if save_access:
        access.save()


def process_assessments(user, xform: XForm, app: CommCareApp, opportunity: Opportunity, blocks: list[dict]):
    """Process assessments from a form received from CommCare HQ.

    :param user: The user who submitted the form.
    :param xform: The deserialized form object.
    :param app: The CommCare app the form belongs to.
    :param opportunity: The opportunity the app belongs to.
    :param blocks: A list of assessment form blocks."""
    for assessment_data in blocks:
        try:
            score = int(assessment_data["user_score"])
        except ValueError:
            raise ProcessingError("User score must be an integer")
        # TODO: should this move to the opportunity to allow better re-use of the app?
        passing_score = app.passing_score
        access = OpportunityAccess.objects.get(user=user, opportunity=opportunity)
        assessment, created = Assessment.objects.get_or_create(
            user=user,
            app=app,
            opportunity=opportunity,
            opportunity_access=access,
            xform_id=xform.id,
            defaults={
                "date": xform.metadata.timeEnd,
                "score": score,
                "passing_score": passing_score,
                "passed": score >= passing_score,
                "app_build_id": xform.build_id,
                "app_build_version": xform.metadata.app_build_version,
            },
        )

        if not created:
            return ProcessingError("Learn Assessment is already completed")

        notify_user_for_scored_assessment.delay(assessment.pk)


def process_deliver_form(user, xform: XForm, app: CommCareApp, opportunity: Opportunity):
    deliver_matches = [
        match.value for match in DELIVER_UNIT_JSONPATH.find(xform.form) if match.value["@xmlns"] == CCC_LEARN_XMLNS
    ]
    for deliver_unit_block in deliver_matches:
        process_deliver_unit(user, xform, app, opportunity, deliver_unit_block)

    task_matches = [
        match.value for match in TASK_MODULE_JSONPATH.find(xform.form) if match.value["@xmlns"] == CCC_LEARN_XMLNS
    ]
    if task_matches:
        process_task_modules(user, xform, app, opportunity, task_matches)


def clean_form_submission(access: OpportunityAccess, user_visit: UserVisit, xform: XForm) -> list[list[str]]:
    """Validate a form submission against the opportunity's verification flags.

    Checks GPS presence, location proximity, catchment areas, submission time window,
    duplicate entities, attachments, form duration, and custom JSON validation rules.
    Returns a list of [flag_code, reason] pairs. May modify user_visit.status as a
    side effect (e.g., resetting duplicate status when the duplicate flag is disabled).
    """
    flags = []
    opportunity_flags, _ = OpportunityVerificationFlags.objects.get_or_create(opportunity=user_visit.opportunity)
    if opportunity_flags.duplicate:
        if user_visit.status == VisitValidationStatus.duplicate:
            flags.append(["duplicate", "A beneficiary with the same identifier already exists"])
    else:
        user_visit.status = VisitValidationStatus.pending
    if opportunity_flags.gps and user_visit.location is None:
        flags.append(["gps", "GPS data is missing"])
    if opportunity_flags.location > 0 and user_visit.location:
        user_visits = (
            UserVisit.objects.filter(opportunity=user_visit.opportunity, deliver_unit=user_visit.deliver_unit)
            .exclude(Q(status=VisitValidationStatus.trial) | Q(entity_id=user_visit.entity_id))
            .values("location")
        )
        cur_lat, cur_lon, *_ = user_visit.location.split(" ")
        for visit in user_visits:
            if visit.get("location") is None:
                continue
            lat, lon, *_ = visit["location"].split(" ")
            dist = distance((lat, lon), (cur_lat, cur_lon))
            if dist.m <= opportunity_flags.location:
                flags.append(["location", f"Visit location is {dist.m}m from another visit"])
                break
    if opportunity_flags.catchment_areas:
        areas = access.catchmentarea_set.filter(active=True)
        if areas:
            within_catchment = False
            if xform.metadata.location is not None:
                cur_lat, cur_lon, *_ = xform.metadata.location.split(" ")
                for area in areas:
                    dist = distance((area.latitude, area.longitude), (cur_lat, cur_lon))
                    if dist.meters < area.radius:
                        within_catchment = True
                        break
            if not within_catchment:
                flags.append(["catchment", "Visit outside worker catchment areas"])
    if (
        opportunity_flags.form_submission_start
        and opportunity_flags.form_submission_start > xform.metadata.timeStart.time()
    ):
        flags.append(["form_submission_period", "Form was submitted before the start time"])
    if (
        opportunity_flags.form_submission_end
        and opportunity_flags.form_submission_end < xform.metadata.timeStart.time()
    ):
        flags.append(["form_submission_period", "Form was submitted after the end time"])

    deliver_unit_flags = DeliverUnitFlagRules.objects.filter(
        opportunity=user_visit.opportunity, deliver_unit=user_visit.deliver_unit
    ).first()
    if deliver_unit_flags is not None:
        if deliver_unit_flags.check_attachments:
            attachments = user_visit.form_json.get("attachments", {})
            attachments.pop("form.xml", None)
            if len(attachments) == 0:
                flags.append(["attachment_missing", "Form was submitted without attachements."])

        if deliver_unit_flags.duration > 0 and xform.metadata.duration < datetime.timedelta(
            minutes=deliver_unit_flags.duration
        ):
            flags.append(["duration", "The form was completed too quickly."])

    form_json_rules = FormJsonValidationRules.objects.filter(
        opportunity=user_visit.opportunity, deliver_unit=user_visit.deliver_unit
    )
    for form_json_rule in form_json_rules:
        json_path = parse(f"$.{form_json_rule.question_path}")
        matches = [
            match.value
            for match in json_path.find(user_visit.form_json)
            if match.value == form_json_rule.question_value
        ]
        if not matches:
            flags.append(["form_value_not_found", f"Form does not satisfy {form_json_rule.name} validation rule."])
    return flags


def process_deliver_unit(user, xform: XForm, app: CommCareApp, opportunity: Opportunity, deliver_unit_block: dict):
    """Process a delivery form submission into a UserVisit and update CompletedWork.

    Acquires a Redis lock per entity to handle concurrent submissions, then:
    1. Creates a UserVisit with initial status based on daily/total/claim limits
    2. Runs verification flag checks via clean_form_submission()
    3. Auto-approves if enabled and no flags are raised
    4. Updates or creates the associated CompletedWork record
    5. Triggers incremental payment recalculation
    """
    deliver_unit = get_or_create_deliver_unit(app, deliver_unit_block)
    try:
        access = OpportunityAccess.objects.get(opportunity=opportunity, user=user)
    except OpportunityAccess.DoesNotExist:
        raise ProcessingError(f"User does not have access to opportunity {opportunity.name}")
    payment_unit = deliver_unit.payment_unit
    if not payment_unit:
        raise ProcessingError(
            f"Payment unit is not configured for the deliver unit: "
            f"{deliver_unit.name} in opportunity: {opportunity.name}"
        )

    claim = OpportunityClaim.objects.get(opportunity_access=access)
    claim_limit = OpportunityClaimLimit.objects.get(opportunity_claim=claim, payment_unit=payment_unit)
    entity_id = deliver_unit_block.get("entity_id")
    entity_name = deliver_unit_block.get("entity_name")

    # handle concurrent form submissions for same entity id
    lock_key = f"visit_processor:{access.id}:{deliver_unit.id}:{entity_id}"
    with try_redis_lock(lock_key, timeout=30, blocking_timeout=5) as acquired, transaction.atomic():
        if not acquired:
            raise ProcessingError("Error processing form, please retry again.")
        counts = (
            UserVisit.objects.filter(opportunity_access=access, deliver_unit=deliver_unit)
            .exclude(status__in=[VisitValidationStatus.over_limit, VisitValidationStatus.trial])
            .aggregate(
                daily=Count("pk", filter=Q(visit_date__date=xform.metadata.timeStart)),
                total=Count("*"),
                entity=Count("pk", filter=Q(entity_id=deliver_unit_block.get("entity_id"), deliver_unit=deliver_unit)),
            )
        )
        user_visit = UserVisit(
            opportunity=opportunity,
            user=user,
            opportunity_access=access,
            deliver_unit=deliver_unit,
            entity_id=entity_id,
            entity_name=entity_name,
            visit_date=xform.metadata.timeStart,
            xform_id=xform.id,
            app_build_id=xform.build_id,
            app_build_version=xform.metadata.app_build_version,
            form_json=xform.raw_form,
            location=xform.metadata.location,
        )
        completed_work_needs_save = False
        today = datetime.date.today()
        paymentunit_startdate = payment_unit.start_date if payment_unit else None
        if opportunity.start_date > today or (paymentunit_startdate and paymentunit_startdate > today):
            completed_work = None
            user_visit.status = VisitValidationStatus.trial
        else:
            completed_work, _ = CompletedWork.objects.get_or_create(
                opportunity_access=access,
                entity_id=entity_id,
                payment_unit=payment_unit,
                defaults={"entity_name": entity_name},
            )
            user_visit.completed_work = completed_work
            if (
                counts["daily"] >= payment_unit.max_daily
                or counts["total"] >= claim_limit.max_visits
                or (today > claim.end_date or (claim_limit.end_date and today > claim_limit.end_date))
            ):
                user_visit.status = VisitValidationStatus.over_limit
                if not completed_work.status == CompletedWorkStatus.over_limit:
                    completed_work.status = CompletedWorkStatus.over_limit
                    completed_work_needs_save = True
            elif counts["entity"] > 0:
                user_visit.status = VisitValidationStatus.duplicate

        if work_area_case_id := deliver_unit_block.get("work_area_id"):
            if is_a_uuid(work_area_case_id):
                try:
                    user_visit.work_area = WorkArea.objects.get(case_id=work_area_case_id, opportunity=opportunity)
                except WorkArea.DoesNotExist:
                    raise ProcessingError("Work area not found")
            else:
                raise ProcessingError(f"Invalid work area case id specified: {work_area_case_id}")

        flags = clean_form_submission(access, user_visit, xform)
        if access.suspended:
            flags.append(["user_suspended", "This user is suspended from the opportunity."])
            user_visit.status = VisitValidationStatus.rejected
            completed_work.status = CompletedWorkStatus.rejected
        if flags:
            user_visit.flagged = True
            user_visit.flag_reason = {"flags": flags}

        if (
            opportunity.auto_approve_visits
            and user_visit.status == VisitValidationStatus.pending
            and not user_visit.flagged
        ):
            user_visit.status = VisitValidationStatus.approved
            user_visit.review_status = VisitReviewStatus.agree

        user_visit.save()

        if not access.last_active or access.last_active < user_visit.visit_date:
            access.last_active = user_visit.visit_date

        if completed_work is not None:
            if completed_work.completed_count > 0 and completed_work.status == CompletedWorkStatus.incomplete:
                completed_work.status = CompletedWorkStatus.pending
                completed_work_needs_save = True
            if completed_work_needs_save:
                completed_work.save()

    update_payment_accrued_for_user(access, incremental=True)
    transaction.on_commit(partial(download_user_visit_attachments.delay, user_visit.id))


def get_or_create_deliver_unit(app, unit_data):
    unit, _ = DeliverUnit.objects.get_or_create(
        app=app,
        slug=unit_data["@id"],
        defaults={
            "name": unit_data["name"],
        },
    )
    return unit


def get_opportunity(domain, hq_server, deliver_app_id=None, learn_app_id=None):
    if not (learn_app_id or deliver_app_id):
        raise ValueError("One of learn_app_id or deliver_app_id along with domain must be provided")
    if learn_app_id:
        kwargs = {
            "learn_app__cc_domain": domain,
            "learn_app__cc_app_id": learn_app_id,
        }
    if deliver_app_id:
        kwargs = {
            "deliver_app__cc_domain": domain,
            "deliver_app__cc_app_id": deliver_app_id,
        }

    try:
        opportunity = Opportunity.objects.get(active=True, end_date__gte=now().date(), **kwargs)
        if learn_app_id:
            app = opportunity.learn_app
        elif deliver_app_id:
            app = opportunity.deliver_app
        if app.hq_server != hq_server:
            raise ProcessingError(f"CommCare App {app.id} not found on {hq_server}")
        return opportunity
    except Opportunity.DoesNotExist:
        pass
    except Opportunity.MultipleObjectsReturned:
        app_id = learn_app_id or deliver_app_id
        raise ProcessingError(f"Multiple active opportunities found for CommCare app {app_id}.")


def get_user(xform: XForm):
    cc_username = _get_commcare_username(xform)
    user = User.objects.filter(connectiduserlink__commcare_username=cc_username).first()
    if not user:
        raise ProcessingError(f"Commcare User {cc_username} not found")
    return user


def _get_commcare_username(xform: XForm):
    username = xform.metadata.username
    if "@" in username:
        return username
    return f"{username}@{xform.domain}.commcarehq.org"
