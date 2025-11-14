from django.db.models import Count, F, OuterRef, Q, Subquery

from commcare_connect.opportunity.models import Assessment, CompletedWork, CompletedWorkStatus, CredentialConfiguration
from commcare_connect.users.models import UserCredential


class UserCredentialIssuer:
    @classmethod
    def run(cls):
        all_cred_levels = UserCredential.DeliveryLevel.choices + UserCredential.LearnLevel.choices
        user_credentials_data = []
        for cred_level, _ in all_cred_levels:
            cred_level_opportunity_ids = CredentialConfiguration.objects.filter(
                Q(learn_level=cred_level) | Q(delivery_level=cred_level)
            ).values_list("opportunity_id", flat=True)

            if cred_level in UserCredential.DeliveryLevel.values:
                user_credentials_data.extend(
                    cls.get_delivery_user_credentials(cred_level_opportunity_ids, cred_level),
                )
            else:
                user_credentials_data.extend(
                    cls.get_learning_user_credentials(cred_level_opportunity_ids, cred_level),
                )

            UserCredential.objects.bulk_create(user_credentials_data, batch_size=100, ignore_conflicts=True)
            user_credentials_data = []
            # Todo: send to PersonalID for credential issuance
            # Ticket: CCCT-1725

    @classmethod
    def get_delivery_user_credentials(cls, opportunities, credential_level):
        level_int = UserCredential.delivery_level_num(credential_level, UserCredential.CredentialType.DELIVERY)
        if not level_int:
            return []

        user_credentials_to_exclude = UserCredential.objects.filter(
            opportunity=OuterRef("opportunity_access__opportunity"),
            credential_type=UserCredential.CredentialType.DELIVERY,
            level=credential_level,
        ).values_list("user_id", flat=True)

        users_earning_credentials = (
            CompletedWork.objects.filter(
                opportunity_access__opportunity__id__in=opportunities,
                status=CompletedWorkStatus.approved,
            )
            .exclude(opportunity_access__user_id__in=Subquery(user_credentials_to_exclude))
            .values("opportunity_access__user_id", "opportunity_access__opportunity_id")
            .annotate(
                deliveries_count=Count("opportunity_access__user_id"),
                opportunity_id=F("opportunity_access__opportunity_id"),
                delivery_type_id=F("opportunity_access__opportunity__delivery_type_id"),
                cred_user_id=F("opportunity_access__user_id"),
            )
            .filter(deliveries_count__gte=level_int)
            .values("cred_user_id", "opportunity_id", "delivery_type_id")
            .distinct()
        )

        return cls._parse_to_user_credential_models(
            credentials_users=users_earning_credentials,
            credential_type=UserCredential.CredentialType.DELIVERY,
            level=credential_level,
        )

    @classmethod
    def get_learning_user_credentials(cls, opportunities, credential_level):
        user_credentials_to_exclude = UserCredential.objects.filter(
            opportunity_id=OuterRef("opportunity_id"),
            credential_type=UserCredential.CredentialType.LEARN,
            level=credential_level,
        ).values_list("user_id", flat=True)

        users_earning_credentials = (
            Assessment.objects.filter(
                opportunity_id__in=opportunities,
                passed=True,
                opportunity_access__isnull=False,
            )
            .exclude(opportunity_access__user_id__in=Subquery(user_credentials_to_exclude))
            .annotate(
                delivery_type_id=F("opportunity__delivery_type_id"),
                cred_user_id=F("opportunity_access__user_id"),
            )
            .values("cred_user_id", "opportunity_id", "delivery_type_id")
            .distinct()
        )
        return cls._parse_to_user_credential_models(
            credentials_users=users_earning_credentials,
            credential_type=UserCredential.CredentialType.LEARN,
            level=credential_level,
        )

    @classmethod
    def _parse_to_user_credential_models(cls, credentials_users, credential_type, level):
        return [
            UserCredential(
                **{
                    "user_id": cred_user["cred_user_id"],
                    "credential_type": credential_type,
                    "level": level,
                    "opportunity_id": cred_user["opportunity_id"],
                    "delivery_type_id": cred_user.get("delivery_type_id", None),
                }
            )
            for cred_user in credentials_users
        ]
