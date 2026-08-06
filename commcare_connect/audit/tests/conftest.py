import pytest

from commcare_connect.audit.tests.factories import AuditReportEntryFactory
from commcare_connect.opportunity.tests.factories import OpportunityAccessFactory, UserFactory


@pytest.fixture
def make_audit_entry():
    """Factory fixture: build an AuditReportEntry for ``report`` with a single
    ``calc_a`` result, for a worker named ``name``.
    """

    def _make(report, name, value, has_data=True, in_range=True, phone_number=None):
        access = OpportunityAccessFactory(user=UserFactory(name=name, phone_number=phone_number))
        return AuditReportEntryFactory(
            audit_report=report,
            opportunity_access=access,
            results={
                "calc_a": {
                    "value": value,
                    "has_sufficient_data": has_data,
                    "in_range": in_range,
                    "label": "Calc A",
                }
            },
        )

    return _make
