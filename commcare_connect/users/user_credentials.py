from itertools import chain

from django.contrib.postgres.aggregates import ArrayAgg
from django.db.models import Count, F, OuterRef, Q, Subquery
from django.utils.timezone import now

from commcare_connect.connect_id_client import add_credentials as add_credentials_on_personalid
from commcare_connect.opportunity.models import Assessment, CompletedWork, CompletedWorkStatus, CredentialConfiguration
from commcare_connect.users.models import UserCredential


class UserCredentialIssuer:
    MAX_CREDENTIALS_PER_REQUEST = 200

    @classmethod
    def run(cls):
        from commcare_connect.opportunity.tasks import submit_credentials_to_personalid_task

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

            UserCredential.objects.bulk_create(user_credentials_data, batch_size=100)
            user_credentials_data = []

        # Decouple the PersonalID submission to a celery task
        submit_credentials_to_personalid_task()

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
            .values("opportunity_access__user_id")
            .annotate(deliveries_count=Count("opportunity_access__user_id"))
            .filter(deliveries_count__gte=level_int)
            .annotate(
                opportunity_id=F("opportunity_access__opportunity_id"),
                delivery_type_id=F("opportunity_access__opportunity__delivery_type_id"),
                cred_user_id=F("opportunity_access__user_id"),
            )
            .values("cred_user_id", "opportunity_id", "delivery_type_id")
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
            )
            .exclude(opportunity_access__user_id__in=Subquery(user_credentials_to_exclude))
            .annotate(
                delivery_type_id=F("opportunity__delivery_type_id"),
                cred_user_id=F("opportunity_access__user_id"),
            )
            .values("cred_user_id", "opportunity_id", "delivery_type_id")
        )
        return cls._parse_to_user_credential_models(
            credentials_users=users_earning_credentials,
            credential_type=UserCredential.CredentialType.LEARN,
            level=credential_level,
        )

    @classmethod
    def issue_credentials_to_users(cls):
        """
        Issue user credentials to PersonalID for all users who have not been issued credentials.
        """

        def parse_credential_payload_item(users_credential):
            return {
                "usernames": users_credential["usernames"],
                "title": UserCredential.get_title(
                    credential_type=users_credential["credential_type"],
                    level=users_credential["level"],
                    delivery_type_name=users_credential["delivery_type__name"]
                    or users_credential["opportunity__name"],
                ),
                "type": users_credential["credential_type"],
                "level": users_credential["level"],
                "slug": users_credential["opportunity__id"],
                "opportunity_id": users_credential["opportunity__id"],
            }

        unissued_credentials_qs = cls._get_unissued_user_credentials_queryset()
        index_in_chunk = 0
        credentials_payload_items = []
        index_to_credential_ids_set_mapper = {}

        for users_credential in unissued_credentials_qs.iterator(chunk_size=cls.MAX_CREDENTIALS_PER_REQUEST):
            index_to_credential_ids_set_mapper[index_in_chunk] = users_credential["credential_ids"]
            credentials_payload_items.append(parse_credential_payload_item(users_credential))
            index_in_chunk += 1

            if index_in_chunk >= cls.MAX_CREDENTIALS_PER_REQUEST:
                cls._submit_credentials_to_personal_id(index_to_credential_ids_set_mapper, credentials_payload_items)
                index_in_chunk = 0
                credentials_payload_items = []
                index_to_credential_ids_set_mapper = {}

        if credentials_payload_items:
            cls._submit_credentials_to_personal_id(index_to_credential_ids_set_mapper, credentials_payload_items)

    @classmethod
    def _get_unissued_user_credentials_queryset(cls):
        """
        This function returns a queryset that groups unissued UserCredential records in a way that the usernames
        of the users sharing the same credential_type, level, opportunity_id, and delivery_type_name are
        aggregated together.

        The relating user credential IDs is also aggregated to more easily find and update the issued_on field
        later after successfully submitting the credentials to PersonalID.
        """
        return (
            UserCredential.objects.filter(issued_on__isnull=True)
            .values("credential_type", "level", "opportunity__id", "opportunity__name", "delivery_type__name")
            .annotate(usernames=ArrayAgg("user__username", distinct=True))
            .annotate(credential_ids=ArrayAgg("id", distinct=True))
            .order_by("credential_type", "level", "opportunity__id", "opportunity__name", "delivery_type__name")
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

    def _submit_credentials_to_personal_id(index_to_credential_ids_set_mapper, credentials_items: list[dict]):
        success_indices = add_credentials_on_personalid(credentials_items)

        successful_credential_ids = list(
            chain.from_iterable(index_to_credential_ids_set_mapper[i] for i in success_indices)
        )
        UserCredential.objects.filter(id__in=successful_credential_ids).update(issued_on=now())
