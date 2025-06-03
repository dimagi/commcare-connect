from enum import Enum

from commcare_connect.opportunity.models import FormJsonValidationRules


class Flags(Enum):
    DUPLICATE = "duplicate"
    GPS = "gps"
    LOCATION = "location"
    CATCHMENT = "catchment"
    FORM_SUBMISSION_PERIOD = "form_submission_period"
    ATTACHMENT_MISSING = "attachment_missing"
    DURATION = "duration"
    FORM_VALUE_NOT_FOUND = "form_value_not_found"
    USER_SUSPENDED = "user_suspended"


class FlagDescription(Enum):
    DUPLICATE = "A beneficiary with the same identifier already exists"
    GPS = "GPS data is missing"
    LOCATION = "Visit location is too close to another visit"
    CATCHMENT = "Visit outside worker catchment areas"
    FORM_SUBMISSION_PERIOD = "Form was submitted before the start time"
    ATTACHMENT_MISSING = "Form was submitted without attachements."
    DURATION = "The form was completed too quickly."
    USER_SUSPENDED = "This user is suspended from the opportunity."

    @staticmethod
    def FORM_VALUE_NOT_FOUND(form_json_rule: FormJsonValidationRules):
        return ["form_value_not_found", f"Form does not satisfy {form_json_rule.name} validation rule."]


class FlagLabels(Enum):
    """Flag Labels Enum to make the flags more presentable to users.

    `FlagLabels.get_label(FLAG_NAME)` is the preferred way to get labels.
    `FLAG_NAME` is the value from the Flags enum and the first element in
    the flag_reason field on UserVisits.
    """

    DUPLICATE = "Duplicate"
    GPS = "GPS"
    LOCATION = "Location"
    CATCHMENT = "Catchment"
    FORM_SUBMISSION_PERIOD = "Off Hours"
    ATTACHMENT_MISSING = "No Attachment"
    DURATION = "Duration"
    FORM_VALUE_NOT_FOUND = "Missing Form Value"
    USER_SUSPENDED = "User Suspended"

    @classmethod
    def get_label(cls, flag):
        name = Flags(flag).name
        return cls[name].value
