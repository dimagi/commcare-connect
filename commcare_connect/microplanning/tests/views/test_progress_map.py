import pytest
from django.utils import translation

from commcare_connect.microplanning.models import WorkAreaStatus
from commcare_connect.microplanning.tests.factories import (
    ImplementationAreaFactory,
    WorkAreaFactory,
    WorkAreaGroupFactory,
)
from commcare_connect.microplanning.views.progress_map import (
    _work_area_detail,
    _work_area_search_options,
)
from commcare_connect.opportunity.models import VisitValidationStatus
from commcare_connect.opportunity.tests.factories import OpportunityAccessFactory, UserVisitFactory


@pytest.mark.django_db
class TestWorkAreaSearchOptions:
    def test_includes_all_three_searchable_types(self, opportunity):
        work_area = WorkAreaFactory(opportunity=opportunity)
        group = WorkAreaGroupFactory(opportunity=opportunity)
        impl_area = ImplementationAreaFactory(opportunity=opportunity)

        assert _work_area_search_options(opportunity) == [
            {
                "value": f"wa:{work_area.id}",
                "label": work_area.slug,
                "type": "Work Area",
                "kind": "wa",
                "object_id": work_area.id,
                "filter_name": "work_area",
            },
            {
                "value": f"wag:{group.id}",
                "label": group.name,
                "type": "Work Area Group",
                "kind": "wag",
                "object_id": group.id,
                "filter_name": "work_area_group",
            },
            {
                "value": f"ia:{impl_area.id}",
                "label": impl_area.name,
                "type": "Implementation Area",
                "kind": "ia",
                "object_id": impl_area.id,
                "filter_name": "implementation_area",
            },
        ]

    def test_kind_is_untranslated_so_styling_can_key_on_it(self, opportunity):
        """``type`` is a translated display string; ``kind`` is the stable key the badge colours
        are chosen by, so it must not change with the active language."""
        WorkAreaFactory(opportunity=opportunity)

        with translation.override("fr"):
            option = _work_area_search_options(opportunity)[0]

        assert option["kind"] == "wa"

    def test_excludes_other_opportunities(self, opportunity):
        WorkAreaFactory()
        WorkAreaGroupFactory()
        ImplementationAreaFactory()

        assert _work_area_search_options(opportunity) == []

    def test_orders_each_type_by_label(self, opportunity):
        WorkAreaFactory(opportunity=opportunity, slug="zeta-area")
        WorkAreaFactory(opportunity=opportunity, slug="alpha-area")
        WorkAreaGroupFactory(opportunity=opportunity, name="Zeta Group")
        WorkAreaGroupFactory(opportunity=opportunity, name="Alpha Group")

        labels = [option["label"] for option in _work_area_search_options(opportunity)]

        assert labels == ["alpha-area", "zeta-area", "Alpha Group", "Zeta Group"]


@pytest.mark.django_db
class TestWorkAreaDetail:
    def test_returns_the_fields_the_map_sidebar_renders(self, opportunity):
        access = OpportunityAccessFactory(opportunity=opportunity)
        group = WorkAreaGroupFactory(opportunity=opportunity)
        impl_area = ImplementationAreaFactory(opportunity=opportunity)
        work_area = WorkAreaFactory(
            opportunity=opportunity,
            opportunity_access=access,
            work_area_group=group,
            implementation_area=impl_area,
            implementation_area_name=impl_area.name,
            status=WorkAreaStatus.NOT_VISITED,
        )

        detail = _work_area_detail(opportunity, work_area.id)

        assert detail == {
            "id": work_area.id,
            "slug": work_area.slug,
            "status": WorkAreaStatus.NOT_VISITED,
            "building_count": work_area.building_count,
            "expected_visit_count": work_area.expected_visit_count,
            "group_id": group.id,
            "group_name": group.name,
            "assignee_name": access.user.name,
            "assignee_phone": access.user.phone_number,
            "visits_completed": 0,
            "implementation_area_name": impl_area.name,
        }

    def test_visits_completed_counts_only_approved_visits(self, opportunity):
        access = OpportunityAccessFactory(opportunity=opportunity)
        work_area = WorkAreaFactory(opportunity=opportunity, opportunity_access=access)
        for status in (
            VisitValidationStatus.approved,
            VisitValidationStatus.approved,
            VisitValidationStatus.rejected,
        ):
            UserVisitFactory(opportunity=opportunity, user=access.user, work_area=work_area, status=status)

        assert _work_area_detail(opportunity, work_area.id)["visits_completed"] == 2

    def test_returns_none_for_another_opportunitys_work_area(self, opportunity):
        other_work_area = WorkAreaFactory()

        assert _work_area_detail(opportunity, other_work_area.id) is None

    def test_returns_none_for_a_missing_work_area(self, opportunity):
        assert _work_area_detail(opportunity, 999999) is None
