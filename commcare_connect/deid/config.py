from dataclasses import dataclass

from django.db import models

from commcare_connect.opportunity.models import CompletedWork, UserInvite, UserVisit
from commcare_connect.users.models import ConnectIDUserLink, User

STRATEGY_DROP_FIELD = "drop_field"


@dataclass
class AnonymizationField:
    field_name: str
    anonymization_strategy: str = STRATEGY_DROP_FIELD


@dataclass
class AnonymizationConfig:
    model: models.Model
    fields: list[AnonymizationField]


def get_fields_to_anonymize() -> list[AnonymizationConfig]:
    """
    Get the fields to anonymize.
    """
    config = {
        User: [
            "name",
            "email",
            "username",
            "phone_number",
        ],
        UserVisit: [
            "form_json",
            "location",
            "entity_name",
            "entity_id",
        ],
        CompletedWork: [
            "entity_name",
            "entity_id",
        ],
        ConnectIDUserLink: [
            "commcare_username",
        ],
        UserInvite: [
            "phone_number",
            "message_sid",
        ],
    }
    return [
        AnonymizationConfig(
            model=model,
            fields=[AnonymizationField(field_name=field_name) for field_name in field_names],
        )
        for model, field_names in config.items()
    ]
