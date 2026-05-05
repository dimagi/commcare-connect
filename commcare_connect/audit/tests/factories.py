import datetime

import factory
from factory.django import DjangoModelFactory

from commcare_connect.audit.models import AuditReport, AuditReportEntry
from commcare_connect.opportunity.tests.factories import OpportunityAccessFactory, OpportunityFactory


class AuditReportFactory(DjangoModelFactory):
    class Meta:
        model = AuditReport

    opportunity = factory.SubFactory(OpportunityFactory)
    period_start = datetime.date(2026, 4, 13)
    period_end = datetime.date(2026, 4, 19)


class AuditReportEntryFactory(DjangoModelFactory):
    class Meta:
        model = AuditReportEntry

    audit_report = factory.SubFactory(AuditReportFactory)
    opportunity_access = factory.SubFactory(OpportunityAccessFactory)
    results = {}
    flagged = False
