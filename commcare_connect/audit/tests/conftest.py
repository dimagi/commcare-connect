import pytest

from commcare_connect.audit.chc_indicators import WorkAreasRemaining
from commcare_connect.audit.tests.factories import AuditReportEntryFactory
from commcare_connect.opportunity.tests.factories import OpportunityAccessFactory, UserFactory


@pytest.fixture
def make_audit_entry():
    """Factory fixture: build an AuditReportEntry for ``report`` with a single
    ``calc_a`` result, for a worker named ``name``.
    """

    def _make(report, name, value, has_data=True, in_range=True, phone_number=None, has_work_areas_remaining=True):
        access = OpportunityAccessFactory(user=UserFactory(name=name, phone_number=phone_number))
        results = {
            "calc_a": {
                "value": value,
                "has_sufficient_data": has_data,
                "in_range": in_range,
                "label": "Calc A",
            }
        }
        # None means this entry predates the calculation, so the metric is omitted.
        if has_work_areas_remaining is not None:
            results[WorkAreasRemaining.name] = {
                "value": has_work_areas_remaining,
                "has_sufficient_data": True,
                "in_range": True,
                "label": WorkAreasRemaining.label,
            }
        return AuditReportEntryFactory(
            audit_report=report,
            opportunity_access=access,
            results=results,
        )

    return _make
