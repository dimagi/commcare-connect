import pytest
from django.urls import reverse

from commcare_connect.solicitations.models import Solicitation
from commcare_connect.solicitations.tests.factories import EOIFactory, RFPFactory, SolicitationFactory


class BaseSolicitationViewTest:
    """Base class for solicitation view tests with common setup"""

    @pytest.fixture(autouse=True)
    def setup(self, client):
        self.client = client


@pytest.mark.django_db
class TestPublicSolicitationListView(BaseSolicitationViewTest):
    def test_public_list_view_shows_active_public_solicitations(self):
        """Test that public list only shows active, publicly listed solicitations"""
        # Create various solicitations using factory
        active_public_eoi = EOIFactory(
            title="Active Public EOI",
            status=Solicitation.Status.ACTIVE,
            is_publicly_listed=True,
        )

        active_private_eoi = EOIFactory(
            title="Active Private EOI",
            status=Solicitation.Status.ACTIVE,
            is_publicly_listed=False,
        )

        draft_public_eoi = EOIFactory(
            title="Draft Public EOI",
            status=Solicitation.Status.DRAFT,
            is_publicly_listed=True,
        )

        url = reverse("solicitations:list")
        response = self.client.get(url)

        assert response.status_code == 200
        content = response.content.decode()
        assert active_public_eoi.title in content
        assert active_private_eoi.title not in content
        assert draft_public_eoi.title not in content

    @pytest.mark.parametrize(
        "solicitation_type,url_name,should_show_eoi,should_show_rfp",
        [
            (Solicitation.Type.EOI, "solicitations:eoi_list", True, False),
            (Solicitation.Type.RFP, "solicitations:rfp_list", False, True),
        ],
    )
    def test_type_filter_views(self, solicitation_type, url_name, should_show_eoi, should_show_rfp):
        """Test that type-specific filter views only show solicitations of that type"""
        # Create EOI and RFP using factories
        eoi = EOIFactory(
            title="Test EOI",
            status=Solicitation.Status.ACTIVE,
            is_publicly_listed=True,
        )

        rfp = RFPFactory(
            title="Test RFP",
            status=Solicitation.Status.ACTIVE,
            is_publicly_listed=True,
        )

        # Test the filter view
        url = reverse(url_name)
        response = self.client.get(url)

        assert response.status_code == 200
        content = response.content.decode()

        if should_show_eoi:
            assert eoi.title in content
        else:
            assert eoi.title not in content

        if should_show_rfp:
            assert rfp.title in content
        else:
            assert rfp.title not in content


@pytest.mark.django_db
class TestPublicSolicitationDetailView(BaseSolicitationViewTest):
    def test_detail_view_shows_active_solicitation(self):
        """Test that detail view shows active solicitation content"""
        solicitation = SolicitationFactory(
            title="Test Solicitation",
            description="Detailed description of the program",
            scope_of_work="Detailed scope of work",
            status=Solicitation.Status.ACTIVE,
        )

        url = reverse("solicitations:detail", kwargs={"pk": solicitation.pk})
        response = self.client.get(url)

        assert response.status_code == 200
        content = response.content.decode()
        assert solicitation.title in content
        assert solicitation.description in content
        assert solicitation.scope_of_work in content

    def test_detail_view_shows_private_solicitation_via_direct_url(self):
        """Private solicitations should be accessible via direct URL"""
        private_solicitation = SolicitationFactory(
            title="Private Solicitation",
            description="This is not publicly listed",
            status=Solicitation.Status.ACTIVE,
            is_publicly_listed=False,
        )

        url = reverse("solicitations:detail", kwargs={"pk": private_solicitation.pk})
        response = self.client.get(url)

        assert response.status_code == 200
        assert private_solicitation.title in response.content.decode()

    def test_detail_view_404_for_draft_solicitation(self):
        """Draft solicitations should not be accessible even via direct URL"""
        draft_solicitation = SolicitationFactory(
            title="Draft Solicitation",
            description="This is a draft",
            status=Solicitation.Status.DRAFT,
        )

        url = reverse("solicitations:detail", kwargs={"pk": draft_solicitation.pk})
        response = self.client.get(url)

        assert response.status_code == 404
