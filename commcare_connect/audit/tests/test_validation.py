"""
Tests for view validation logic.
"""

import json

import pytest
from django.test import TestCase
from django.urls import reverse

from commcare_connect.users.tests.factories import UserFactory


@pytest.mark.django_db
class AuditViewValidationTest(TestCase):
    """
    Test audit view input validation.
    """

    def setUp(self):
        """Set up authenticated user"""
        self.user = UserFactory(username="test_auditor", is_staff=True)
        self.client.force_login(self.user)

    def test_missing_required_data_validation(self):
        """Test that views validate required data"""
        response = self.client.post(
            reverse("audit:create_session"),
            data=json.dumps({"opportunities": [], "criteria": {"type": "last_n_per_flw"}}),
            content_type="application/json",
        )

        assert response.status_code == 400
        data = response.json()
        assert "error" in data
