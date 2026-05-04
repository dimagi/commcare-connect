import datetime
import random
import uuid

import factory
from django.contrib.gis.geos import Point, Polygon
from factory import Sequence, SubFactory
from factory.django import DjangoModelFactory

from commcare_connect.microplanning.models import SRID, WorkArea, WorkAreaGroup, WorkAreaInaccessibilityRequest
from commcare_connect.opportunity.tests.factories import OpportunityAccessFactory, OpportunityFactory


class WorkAreaGroupFactory(DjangoModelFactory):
    opportunity = SubFactory(OpportunityFactory)
    opportunity_access = SubFactory(OpportunityAccessFactory)
    ward = Sequence(lambda n: f"ward-{n}")
    name = Sequence(lambda n: f"group-{n}")

    class Meta:
        model = WorkAreaGroup


class WorkAreaFactory(DjangoModelFactory):
    opportunity = SubFactory(OpportunityFactory)
    slug = Sequence(lambda n: f"area-{n}")
    ward = Sequence(lambda n: f"ward-{n}")

    @factory.lazy_attribute
    def case_id(self):
        return str(uuid.uuid4())

    @factory.lazy_attribute
    def centroid(self):
        return Point(
            random.uniform(77, 78),
            random.uniform(28, 29),
            srid=SRID,
        )

    @factory.lazy_attribute
    def boundary(self):
        x1, y1 = 77, 28
        x2, y2 = 78, 29
        return Polygon(
            (
                (x1, y1),
                (x2, y1),
                (x2, y2),
                (x1, y2),
                (x1, y1),
            ),
            srid=SRID,
        )

    building_count = Sequence(lambda n: n + 1)
    expected_visit_count = Sequence(lambda n: n + 2)

    class Meta:
        model = WorkArea


class WorkAreaInaccessibilityRequestFactory(DjangoModelFactory):
    work_area = SubFactory(WorkAreaFactory)

    @factory.lazy_attribute
    def opportunity_access(self):
        return OpportunityAccessFactory(opportunity=self.work_area.opportunity)

    xform_id = factory.LazyFunction(lambda: str(uuid.uuid4()))
    date_of_visit = factory.LazyFunction(datetime.date.today)
    location = None
    reason = factory.Faker("sentence", nb_words=3)
    additional_details = ""
    estimated_duration = ""

    class Meta:
        model = WorkAreaInaccessibilityRequest
