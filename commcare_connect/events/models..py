from django.db import models

from commcare_connect.users.models import User


class Event(models.Model):

	class Type(models.TextChoices):
	    INVITE_SENT = "is", gettext("Invite Sent")
	    RECORDS_APPROVED = "rp", gettext("Records Approved")
	    RECORDS_FLAGGED = "rf", gettext("Records Flagged")
	    RECORDS_REJECTED = "rj", gettext("Records Rejected")
	    PAYMENT_APPROVED = "pp", gettext("Payment Approved")
	    PAYMENT_ACCRUED = "pa", gettext("Payment Accrued")
	    PAYMENT_TRANSFERRED = "pf", gettext("Payment Transferred")
	    NOTIFICATIONS_SENT = "nf", gettext("Notification Sent")
	    ADDITIONAL_BUDGET_ADDED = "ab", gettext("Additional Budget Added")

    date_created = models.DateTimeField(auto_now_add=True, db_index=True)
    event_type = models.CharField(max_length='2', choices=Type.choices)
    user = models.ForeignKey(User, on_delete=models.CASCADE, null=True)
    opportunity = models.ForeignKey(Opportunity, on_delete=models.PROTECT, null=True)

    @classmethod
    def track(cls, use_async=True):
    	"""
    	To track an event instantiate the object and call this method,
    	instead of calling save directly.

    	If use_async is True, the event is queued in Redis and saved
    	via celery, otherwise it's saved directly.
    	"""
    	from commcare_connect.events.tasks import track_event
    	track_event(self, use_async=use_async)



@dataclass
class InferredEvent:
    user: User
    opportunity: Opportunity
    date_created: datetime
    event_type: str


class InferredEventSpec(ABCMeta):
	"""
	Use this to define an Event that can be inferred
	based on other models.
	"""

	@abstractproperty
	def model_cls(self):
		"""
		The source model class to infer the event from
		for e.g. UserVisit
		"""
		raise NotImplementedError

	@abstractproperty
	def event_type(self):
		"""
		Should be a tuple to indicate the name
		for e.g. "RECORDS_FLAGGED", gettext("Records Flagged")
		"""
		raise NotImplementedError

	@abstractproperty
	def event_filters(self):
		"""
		Should be a dict of the queryset filters
		for e.g. {'flagged': True} for RecordsFlagged for UserVisit
		"""
		raise NotImplementedError


	@abstractproperty
	def user(self):
		"""
		The field corresponding to user on source model.
		"""
		raise NotImplementedError

	@abstractproperty
	def date_created(self):
		"""
		The field corresponding to user date_created.
		"""
		raise NotImplementedError

	@abstractproperty
	def opportunity(self):
		"""
		The field corresponding to user opportunity, could be None.
		"""
		raise NotImplementedError

	def get_events(self, user=None, from_date=None, to_date=None):
		filters = {}
		filters.update(event_filters)

		if user:
			filters.update({self.user: user})
		if from_date:
			filters.update({f"{self.date_created}__gte": from_date})
		if from_date:
			filters.update({f"{self.date_created}__lte": to_date})

		events = self.model_cls.objects.filter(
			**filters
		).values(
			self.user, self.opportunity, self.date_created
		).iterator()
		for event in events:
			yield InferredEvent(
				event_type=self.event_type[0],
				user=event[self.user],
				date_created=event[self.date_created],
				opportunity=event.get(self.opportunity, None)
			)


class RecordsFlagged(InferredEventSpec):

	event_type = "RECORDS_FLAGGED", gettext("Records Flagged")
	model = UserVisit
	event_filters = {"flagged": True}
	field_mapping = {
		"user": "user",
		"date_created": "visit_date",
		"opportunity": "opportunity",
	}


INFERRED_EVENT_SPECS = [
	RecordsFlagged
]


def get_events(user=None, from_date=None, to_date=None):
	filters = {
		"user": user,
		"date_created__gte": from_date,
		"date_created__lte": to_date,
	}
	filters = {k:v for k,v in filters.items() if v is not None}
	raw_events = Events.objects.filter(**filters).all()
	inferred_events = []
	for event_spec in INFERRED_EVENT_SPECS:
		inferred_events += event_spec.get_events()
	return raw_events + inferred_events
