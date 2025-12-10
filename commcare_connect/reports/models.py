from django.db import models

from commcare_connect.users.models import User


class UserAnalyticsData(models.Model):
    username = models.CharField(unique=True)
    # user can be null in cases where a user is only registered on PersonalID
    # and is not a Connect User, the username field can be used as the
    # user identifier in those cases.
    user = models.OneToOneField(User, on_delete=models.CASCADE, null=True)

    has_opp_invite = models.DateTimeField(null=True)
    has_ever_earned_payment = models.DateTimeField(null=True)
    has_started_learning = models.DateTimeField(null=True)
    has_completed_learning = models.DateTimeField(null=True)
    has_completed_assessment = models.DateTimeField(null=True)
    has_claimed_job = models.DateTimeField(null=True)
    has_started_job = models.DateTimeField(null=True)
    has_been_paid = models.DateTimeField(null=True)
    has_completed_opp = models.DateTimeField(null=True)
    has_completed_multiple_opps = models.DateTimeField(null=True)
    has_offered_multiple_opps = models.DateTimeField(null=True)
    has_accepted_multiple_opps = models.DateTimeField(null=True)
    has_sso_on_hq_app = models.DateTimeField(null=True)
