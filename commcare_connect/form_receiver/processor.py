import datetime

from django.db.models import Count, Q
from django.utils.timezone import now
from geopy.distance import distance
from jsonpath_ng import JSONPathError
from jsonpath_ng.ext import parse

from commcare_connect.form_receiver.const import CCC_LEARN_XMLNS
from commcare_connect.form_receiver.exceptions import ProcessingError
from commcare_connect.form_receiver.serializers import XForm
from commcare_connect.opportunity.models import (
    Assessment,
    CommCareApp,
    CompletedModule,
    CompletedWork,
    DeliverUnit,
    LearnModule,
    Opportunity,
    OpportunityAccess,
    OpportunityClaim,
    UserVisit,
    VisitValidationStatus,
)
from commcare_connect.opportunity.tasks import download_user_visit_attachments
from commcare_connect.users.models import User

LEARN_MODULE_JSONPATH = parse("$..module")
ASSESSMENT_JSONPATH = parse("$..assessment")
DELIVER_UNIT_JSONPATH = parse("$..deliver")


def process_xform(xform: XForm):
    """Process a form received from CommCare HQ."""
    app = get_app(xform.domain, xform.app_id)
    user = get_user(xform)

    opportunity = get_opportunity(deliver_app=app)
    if opportunity:
        process_deliver_form(user, xform, app, opportunity)

    opportunity = get_opportunity(learn_app=app)
    if opportunity:
        process_learn_form(user, xform, app, opportunity)


def process_learn_form(user, xform: XForm, app: CommCareApp, opportunity: Opportunity):
    processors = [
        (LEARN_MODULE_JSONPATH, process_learn_modules),
        (ASSESSMENT_JSONPATH, process_assessments),
    ]
    for jsonpath, processor in processors:
        try:
            matches = [match.value for match in jsonpath.find(xform.form) if match.value["@xmlns"] == CCC_LEARN_XMLNS]
            if matches:
                processor(user, xform, app, opportunity, matches)
        except JSONPathError as e:
            raise ProcessingError from e


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


def process_learn_modules(user, xform: XForm, app: CommCareApp, opportunity: Opportunity, blocks: list[dict]):
    """Process learn modules from a form received from CommCare HQ.

    :param user: The user who submitted the form.
    :param xform: The deserialized form object.
    :param app: The CommCare app the form belongs to.
    :param opportunity: The opportunity the app belongs to.
    :param blocks: A list of learn module form blocks."""
    for module_data in blocks:
        module = get_or_create_learn_module(app, module_data)
        completed_module, created = CompletedModule.objects.get_or_create(
            user=user,
            module=module,
            opportunity=opportunity,
            defaults={
                "xform_id": xform.id,
                "date": xform.received_on,
                "duration": xform.metadata.duration,
                "app_build_id": xform.build_id,
                "app_build_version": xform.metadata.app_build_version,
            },
        )

        if not created:
            raise ProcessingError("Learn Module is already completed")


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
        assessment, created = Assessment.objects.get_or_create(
            user=user,
            app=app,
            opportunity=opportunity,
            xform_id=xform.id,
            defaults={
                "date": xform.received_on,
                "score": score,
                "passing_score": passing_score,
                "passed": score >= passing_score,
                "app_build_id": xform.build_id,
                "app_build_version": xform.metadata.app_build_version,
            },
        )

        if not created:
            return ProcessingError("Learn Assessment is already completed")


def process_deliver_form(user, xform: XForm, app: CommCareApp, opportunity: Opportunity):
    matches = [
        match.value for match in DELIVER_UNIT_JSONPATH.find(xform.form) if match.value["@xmlns"] == CCC_LEARN_XMLNS
    ]
    if matches:
        for deliver_unit_block in matches:
            process_deliver_unit(user, xform, app, opportunity, deliver_unit_block)


def process_deliver_unit(user, xform: XForm, app: CommCareApp, opportunity: Opportunity, deliver_unit_block: dict):
    deliver_unit = get_or_create_deliver_unit(app, deliver_unit_block)
    access = OpportunityAccess.objects.get(opportunity=opportunity, user=user)
    counts = (
        UserVisit.objects.filter(opportunity=opportunity, user=user)
        .exclude(status=VisitValidationStatus.over_limit)
        .aggregate(
            daily=Count("pk", filter=Q(visit_date__date=xform.metadata.timeStart)),
            total=Count("*"),
            entity=Count("pk", filter=Q(entity_id=deliver_unit_block.get("entity_id"), deliver_unit=deliver_unit)),
        )
    )
    claim = OpportunityClaim.objects.get(opportunity_access__opportunity=opportunity, opportunity_access__user=user)
    entity_id = deliver_unit_block.get("entity_id")
    entity_name = deliver_unit_block.get("entity_name")
    completed_work, _ = CompletedWork.objects.get_or_create(
        opportunity_access=access,
        entity_id=entity_id,
        payment_unit=deliver_unit.payment_unit,
        defaults={
            "entity_name": entity_name,
        },
    )
    user_visit = UserVisit(
        opportunity=opportunity,
        user=user,
        deliver_unit=deliver_unit,
        entity_id=entity_id,
        entity_name=entity_name,
        visit_date=xform.metadata.timeStart,
        xform_id=xform.id,
        app_build_id=xform.build_id,
        app_build_version=xform.metadata.app_build_version,
        form_json=xform.raw_form,
        location=xform.metadata.location,
        completed_work=completed_work,
    )
    if (
        counts["daily"] >= opportunity.daily_max_visits_per_user
        or counts["total"] >= claim.max_payments
        or datetime.date.today() > claim.end_date
    ):
        user_visit.status = VisitValidationStatus.over_limit
    flags = []
    if counts["entity"] > 0:
        user_visit.status = VisitValidationStatus.duplicate
        flags.append(["duplicate", "A beneficiary with the same identifier already exists"])
    if xform.metadata.duration < datetime.timedelta(seconds=60):
        flags.append(["duration", "The form was completed too quickly."])
    if xform.metadata.location is None:
        flags.append(["gps", "GPS data is missing"])
    else:
        user_visits = UserVisit.objects.filter(opportunity=opportunity, deliver_unit=deliver_unit).values("location")
        cur_lat, cur_lon, *_ = xform.metadata.location.split(" ")
        for visit in user_visits:
            if visit.get("location") is None:
                continue
            lat, lon, *_ = visit["location"].split(" ")
            dist = distance((lat, lon), (cur_lat, cur_lon))
            if dist.m <= 10:
                flags.append(["location", "Visit location is too close to another visit"])
                break
    if flags:
        user_visit.flagged = True
        user_visit.flag_reason = {"flags": flags}
    user_visit.save()
    download_user_visit_attachments.delay(user_visit.id)


def get_or_create_deliver_unit(app, unit_data):
    unit, _ = DeliverUnit.objects.get_or_create(
        app=app,
        slug=unit_data["@id"],
        defaults={
            "name": unit_data["name"],
        },
    )
    return unit


def get_opportunity(*, learn_app=None, deliver_app=None):
    if not learn_app and not deliver_app:
        raise ValueError("One of learn_app or deliver_app must be provided")

    kwargs = {}
    if learn_app:
        kwargs = {"learn_app": learn_app}
    if deliver_app:
        kwargs = {"deliver_app": deliver_app}

    try:
        return Opportunity.objects.get(active=True, end_date__gte=now().date(), **kwargs)
    except Opportunity.DoesNotExist:
        pass
    except Opportunity.MultipleObjectsReturned:
        app = learn_app or deliver_app
        raise ProcessingError(f"Multiple active opportunities found for CommCare app {app.cc_app_id}.")


def get_app(domain, app_id):
    app = CommCareApp.objects.filter(cc_domain=domain, cc_app_id=app_id).first()
    if not app:
        raise ProcessingError(f"CommCare app {app_id} not found.")
    return app


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
